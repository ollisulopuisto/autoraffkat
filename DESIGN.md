# Rakenne

Tämä kuvaa miksi koodi on jaettu niin kuin se on. `README.md` kertoo miten
sovellusta käytetään, `CLAUDE.md` mitä ei saa rikkoa.

## Vaatimus joka määrää kaiken

Käyttäjän silmukka on:

1. synkkaa Final Cutissa, vie XML
2. nimeä raidat, säädä liukusäätimiä
3. vie XML, tuo Final Cutiin, katso
4. jos ei kelpaa, takaisin kohtaan 2

Kohdan 2 säädön ja kohdan 3 viennin väliin saa jäädä sekunti. Kaksi tuntia
materiaalia on 360 000 analyysiaskelta, ja äänen purku ffmpegillä kestää
minuutteja. Kumpaakaan ei siis saa tehdä säädön yhteydessä. Tästä seuraa koko
muu rakenne.

## Kerrokset

```
   FCPXML  ──►  fcpxml/read.py  ──►  Timeline (MediaItem + Placement)
                                          │
                    ┌─────────────────────┴──────────────────────┐
                    │                                            │
              audio/envelope.py                             käyttäjän roolit
              ffmpeg + RMS 20 ms                            ja säätimet
              levyvälimuisti                                     │
              SEKUNTEJA                                          │
                    │                                            │
                    └──────────►  analysis.py  ◄─────────────────┘
                                  kohdistus ruudukolle
                                  MILLISEKUNTEJA
                                          │
                                          ▼
                                     decide.py
                                     kynnykset, kestot, päällekkäispuhe
                                     MILLISEKUNTEJA
                                          │
                          ┌───────────────┴───────────────┐
                          ▼                               ▼
                    preview.py                     fcpxml/write.py
                    palkki selaimeen               uusi projekti
```

Raja kulkee `envelope.py`:n ja `analysis.py`:n välissä. Kaikki sen alapuolinen
ajetaan uudestaan joka kerta kun liukusäädintä liikautetaan.

Mitattu kahden tunnin syötteellä: `decide.py` 11 ms (sääntö *laaja*), 38 ms
(sääntö *vahvempi voittaa*, jossa on ylimääräinen lajittelu puhujien yli).
Palvelimen koko kierros fixturella 4,5 ms.

## Verhokäyrä

`envelope.py` purkaa äänen ffmpegillä monoksi 8 kHz:iin ja laskee RMS:n 20 ms
välein desibeleinä. 8 kHz riittää puheen energialle ja neljännestää purkuajan
verrattuna 48 kHz:iin; taajuusvaste ei kiinnosta, koska päätös katsoo vain
tasoa.

Purku tehdään virtana 82 sekunnin paloissa, joten kahden tunnin tiedosto ei
varaa 230 megatavua muistia. Tulos on 360 000 float32-arvoa eli 1,4 MB.

Käyrä indeksoidaan **tiedoston alusta**, ei aikajanasta. Näin sama välimuisti
kelpaa vaikka klippi siirtyisi aikajanalla tai sama tiedosto esiintyisi
useassa projektissa. Aikajanalle siirto tapahtuu vasta `analysis.align`:ssa.

Välimuistin avain on polku, tiedoston koko, muokkausaika ja laskennan
parametrit. Korvattu tiedosto ei siis osu vanhaan käyrään.

## Kohdistus

`analysis.align` muuntaa käyrän aikajanan ruudukolle. Media voi esiintyä
aikajanalla useammassa palassa (`MediaItem.placements`), joten kohdistus
tehdään palasittain: kunkin palan sisällä kuvaus on lineaarinen, joten se on
yksi `np.arange` ja yksi indeksointi.

Samalla syntyy `valid`-maski, joka kertoo missä ruudukon kohdissa mediaa
ylipäätään on. Ilman sitä puuttuva alue näyttäisi hiljaisuudelta, mikä ei ole
sama asia.

## Herkkyys ja vahvistus

Herkkyys on kynnys **pohjakohinan yli**, ei absoluuttinen desibeliarvo:

```
on = db > pohjakohina + herkkyys
```

Vahvistus lisätään desibeleihin, mutta myös pohjakohina siirtyy saman verran,
joten vahvistus supistuu pois yllä olevasta ehdosta. Se vaikuttaa siis vain
siihen, kumpi mikki katsotaan kovemmaksi:

```
taso = db + vahvistus          # vain päällekkäispuheen vertailuun
```

Tämä on tahallista. Jos vahvistus vaikuttaisi myös kynnykseen, kaksi säädintä
tekisi osittain samaa asiaa ja säätäminen muuttuisi arvailuksi.

Pohjakohina on aineiston 20. persentiili. Se lasketaan kerran kohdistuksen
yhteydessä ja jää välimuistiin, koska se ei riipu säätimistä.

## Päätös

`decide.py` ei silmukoi näytteiden yli. Ensin numpy tuottaa `want`-taulukon —
kunkin hetken toivottu kuva ilman kestorajoituksia — ja sitten silmukka kulkee
sen **jaksojen** (`_runs`) yli. Kahden tunnin aineistossa jaksoja on tuhansia,
näytteitä satojatuhansia.

Järjestys:

1. **Vahvistusaika.** Puhejaksot jotka ovat lyhyempiä kuin vahvistusaika
   pudotetaan (`_open_runs`). Alle vahvistusajan mittaiset tauot täytetään
   (`_close_gaps`), jotta sanavälit eivät pilko jaksoa.
2. **Yksi äänessä** → hänen lähikuvansa.
3. **Useampi äänessä.** Jos päällekkäisyys on lyhyempi kuin `min_overlap`, se
   ei ole päällekkäispuhetta vaan myötäilyä: valitaan kovempi eikä laukaista
   sääntöä. Muuten sovelletaan valittua sääntöä.
4. **Puhuja ilman lähikuvaa** → laaja. **Lähikuva jota ei ole aikajanalla
   tässä kohtaa** → pidä nykyinen.
5. **Kestorajoitukset** jaksosilmukassa: ennakko siirtää leikkausta taaksepäin,
   lyhin kuvan kesto estää sitä osumasta liian lähelle edellistä. Jos molemmat
   yhdessä työntävät leikkauksen jakson yli, leikkausta ei tehdä.
6. **Laajan pakotusväli** jälkikäsittelynä: pitkät lähikuvat pilkotaan
   vuorotellen laajaan, mutta ei niin että syntyisi vähimmäiskestoa lyhyempi
   pala.

## Aika

Kaikki XML:stä luettu ja XML:ään kirjoitettu aika on `Fraction`. Syy näkyy
testissä `test_quantize_is_exact_over_many_frames`: 29,97 fps:n kehyksen kesto
on 1001/30000 sekuntia, ja liukulukuna 216 000 kehyksen yli kertyvä virhe
riittää siirtämään leikkauksen väärään kehykseen. Aikajanalle jäisi aukkoja,
ja Final Cut näyttää aukot mustana.

Liukuluku kelpaa vain analyysikerroksessa, jossa 20 ms:n ruudukko on joka
tapauksessa karkeampi kuin kehys.

### FCPXML:n aikasemantiikka

Klipin `offset` on **isännän paikallisessa aikapohjassa**, jonka nollakohta on
isännän `start`. Lapsen absoluuttinen paikka aikajanalla on siis:

```
lapsen_absoluuttinen = isännän_absoluuttinen + (lapsen_offset - isännän_start)
```

Tämä koskee sekä liitettyjä klippejä että `sync-clip`in sisältöä, ja se on
`read.py`:n `_walk`-funktion koko idea. Sama sääntö toiseen suuntaan selittää,
miksi `write.py` antaa mikkien liitetyille klipeille offsetiksi ensimmäisen
spine-klipin `start`-arvon eikä nollaa.

## Kirjoitus

Yksi spine, yksi klippi per kuva. Kameroiden oma ääni pois
`srcEnable="video"`. Mikit liitettyinä klippeinä ensimmäiseen spine-klippiin
laneilla −1, −2, … rooleilla `dialogue.<puhuja>`.

Kvantisointi (`_quantize`) on tarkempi kuin miltä näyttää. Se kulkee jaksot
läpi eteenpäin ja pitää kirjaa kursorista, joka takaa että jokainen kuva saa
vähintään yhden kehyksen ja että seuraavan alku on aina edellistä suurempi.
Jokaisen jakson loppu on seuraavan alku, joten aukkoja ei voi syntyä. Jos
leikkauksia olisi enemmän kuin kehyksiä — mitä päätöskerros ei tuota, mutta
mitä ei saa myöskään kirjoittaa rikkinäisenä — loput pudotetaan ja edellinen
kuva jatkuu niiden yli.

## Käyttöliittymä

FastAPI ja tavallinen JavaScript ilman käännösvaihetta. Selain pitää tilaa vain
säätimistä; päätös ajetaan aina palvelimella, koska se on numpya.

Säätimen liike ei lähetä pyyntöä heti vaan 45 ms:n viiveellä, ja edellinen
pyyntö keskeytetään `AbortController`illa. Raahaus ei siis kasaa jonoa.

Esikatselupalkki tiivistetään palvelimella (`preview.py`) noin 1400 sarakkeeksi.
Puhujarivillä sarake on "äänessä" jos puhuja on äänessä missä tahansa sen
sisällä — muuten lyhyet repliikit katoaisivat tiivistyksessä. Valitun kuvan
rivillä otetaan sarakkeen keskikohta, koska siinä kiinnostaa vallitseva arvo.

### Miksi ei SwiftUI

AVFoundation olisi antanut toiston ja aaltomuodot valmiina. Vastapainona
analyysi olisi pitänyt kirjoittaa Swiftinä uusiksi tai ajaa Pythonia
alaprosessina, eli kaksi kieltä ja IPC ensimmäisestä versiosta alkaen. Tämän
kokoluokan työkalussa se maksaa enemmän kuin tuo.

### Miten toisto lisätään myöhemmin

Päätöskerrokseen ei tarvitse koskea. `preview.py` palauttaa jo aikajanan
sekunteina ja `decide.py` ei tiedä käyttöliittymästä mitään. Tarvitaan:

1. proxytiedostojen luonti ffmpegillä (samaan välimuistihakemistoon)
2. reitti joka tarjoilee proxyn `Range`-tuella
3. `<video>`-elementti ja soitinpää palkin päälle
4. leikkauskohdissa lähteen vaihto, koska yksi `<video>` ei voi näyttää kahta
   kameraa — käytännössä kaksi päällekkäistä elementtiä, joista toista
   esiladataan
