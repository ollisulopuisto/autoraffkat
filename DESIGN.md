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
6. **Pitkä puheenvuoro** jälkikäsittelynä (`_force_wide`).

### Pitkä puheenvuoro

Kohdat 1–5 tuottavat oikean kuvan mutta eivät rytmiä: yksinpuhelu antaa yhden
lähikuvan niin pitkäksi kuin puhe kestää, ja katsojalle se on minuutti samaa
kasvokuvaa. Kun sama puhuja on pitänyt lattiaa `wide_every` sekuntia, kuva
vaihtuu laajaan.

Jatkoja on kaksi, koska ne ovat eri asia leikkauksellisesti eikä kumpikaan ole
aina oikein. **Palaa puhujaan** antaa laajan kestää `wide_hold` ja palaa samaan
kuvaan; rytmi pysyy puhujassa, ja se sopii keskustelulle jossa monologi on
poikkeus. **Jää laajaan** pitää laajan seuraavaan puheenvuoroon asti; pitkä
yksinpuhelu näyttää tilanteelta eikä kasvokuvalta, ja leikkauksia tulee
selvästi vähemmän. Valinta on makua, joten se on säädin eikä vakio.

Tämä ajetaan vasta valmiille leikkauslistalle eikä `want`-taulukkoon. Se on
rytmisääntö eikä havainto siitä kuka puhuu, eikä se saa sekaantua kynnyksiin:
`want` kertoo edelleen kenen vuoro on, ja `_force_wide` päättää erikseen
näytetäänkö se.

Laajan kesto nostetaan aina vähintään lyhimpään kuvan kestoon. Muuten säädin
tuottaisi välähdyksiä, joita mikään muu sääntö ei päästäisi läpi — ja
vähimmäiskesto on koko päätöksen tiukin lupaus.

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

### Monikamera

`<mc-clip>` on isäntä, sisältö on `<media><multicam>`:in kulmissa, ja kulmien
aikapohjan nollakohta on multicamin `tcStart`. Sama sääntö siis pätee, mutta
yhdellä lisäyksellä: **kulman sisältö on rajattava `mc-clip`:n kestoon**.
Kulma ulottuu koko multicamin yli, joten ilman rajausta kaksi osaa samasta
multicamista tuottaisi päällekkäiset esiintymät, verhokäyrä kohdistuisi
väärään kohtaan ja peitto näyttäisi kuvaa siellä missä sitä ei ole. Tämä on
`_walk`:n `bounds`-parametri, ja se koskee myös `ref-clip`iä.

### Raita, ei media

Roolituksen yksikkö on **raita** (`Timeline.tracks`), ei media. Tavallisessa
aikajanassa ero ei näy — jokainen media on oma raitansa ja avain on
tiedostonimi kuten ennen — mutta monikamerassa sama kulma on eri tiedosto joka
osassa, ja ne kuuluvat samaan rooliin, samaan säätimeen ja samaan puhujaan.

Ilman tätä `Roles.wide_key` ja `Roles.closes` pitäisi kaikki muuttaa listoiksi
ja jokainen niitä lukeva kohta osaisi käsitellä monta avainta. Raita hoitaa
saman yhdessä paikassa: päätöskerros näkee edelleen yhden avaimen kuvaa
kohden, ja peitto on raidan osien yhdiste.

Ryhmittely tapahtuu kulman nimellä (`"1"`, `"nyman a Track2"`), koska se on
leikkaajan oma merkintä siitä että kyse on samasta kamerasta. Avain sen sijaan
johdetaan tiedostonimistä, koska nimet ja `angleID`:t vaihtuvat viennistä
toiseen. Kahta saman multicamin kulmaa ei koskaan yhdistetä, vaikka nimet
normalisoituisivat samoiksi.

## Kirjoitus

Yksi spine, yksi klippi per kuva. Kameroiden oma ääni pois
`srcEnable="video"`. Mikit liitettyinä klippeinä ensimmäiseen spine-klippiin
laneilla −1, −2, … rooleilla `dialogue.<puhuja>`.

### Monikameran kirjoitus

Monikameralähteestä ulos tulee `<mc-clip>` per kuva: kuvakulma
`srcEnable="video"`, mikkikulmat `srcEnable="audio"` omilla
`dialogue.<puhuja>`-rooleillaan, kameran oma ääni `active="0"`. Tulos on
natiivi monikameraleikkaus, joten kuvakulman voi vaihtaa Final Cutissa
jälkikäteen — littana leikkauksessa se ei enää onnistu.

Resurssit **kopioidaan lähde-XML:stä sellaisenaan** eikä rakenneta uudestaan.
Multicamin kulmarakenne, `angleID`:t ja assettien keskinäinen synkkaus ovat
juuri se osa jota ei saa muuttaa, ja kopio on ainoa tapa taata se.

Kuva ei saa jatkua osasta toiseen: seuraava osa on eri `<mc-clip>` eri
`angleID`:illä. Siksi kvantisoidut jaksot pilkotaan vielä osien rajoilla
(`_split_spans`), ja jokainen pala saa oman `start`-arvonsa oman osansa
aikapohjassa.

Kvantisointi (`_quantize`) on tarkempi kuin miltä näyttää. Se kulkee jaksot
läpi eteenpäin ja pitää kirjaa kursorista, joka takaa että jokainen kuva saa
vähintään yhden kehyksen ja että seuraavan alku on aina edellistä suurempi.
Jokaisen jakson loppu on seuraavan alku, joten aukkoja ei voi syntyä. Jos
leikkauksia olisi enemmän kuin kehyksiä — mitä päätöskerros ei tuota, mutta
mitä ei saa myöskään kirjoittaa rikkinäisenä — loput pudotetaan ja edellinen
kuva jatkuu niiden yli.

## Ääni

Kolmas hidas kerros: `audio/chain.py` tekee signaalinkäsittelyn,
`audio/mix.py` päättää mitä käsitellään ja vahtii synkkaa.

Ketju ajettiin aluksi rinnakkaisprojektin (automixer) ympäristössä
`uv run --project`illa, koska se vaati Python 3.13:n ja MLX:n. Riippuvuus
purettiin: tarvittu osa oli pieni, ja pedalboard tekee sen suoraan samassa
prosessissa. Mukana lähtivät sekä versiovaatimus että prosessiraja.

Kirjastosta löytyi kaksi kohtaa joissa nimi ei vastaa käytöstä, ja molemmat
olisivat menneet läpi huomaamatta ilman pituustarkistusta:

* `plugin.process(..., reset=False)` jättää liitännäisen viiveen verran häntää
  pois — dxRevivellä 4641 näytettä. Tulos on oikean kuuloinen mutta liian
  lyhyt. Siksi `reset=True`, eikä tiedostoa käsitellä paloissa.
* `pedalboard.Limiter` tekee makeup-vahvistuksen. Se nosti valmiiksi
  normalisoidun raidan −20 LUFS:sta −15,8:aan ja huiput nollaan. Tilalla on
  `peak_guard`: staattinen vaimennus, joka vaimentaa vain jos katto ylittyy
  eikä koskaan nosta.

Portatusta `declick`istä löytyi kolmas: alkuperäinen vertasi HF-energiaa
paikalliseen **maksimiin**, vaikka kommentti puhui keskiarvosta. Naksu on
määritelmän mukaan oman ympäristönsä maksimi, joten ehto ei voinut täyttyä
koskaan ja koko käsittely oli nolla-operaatio. Keskiarvolla se toimii.

### Miksi analyysi ajetaan raa'asta äänestä

Kompressori tekee kaksi asiaa, jotka molemmat huonontavat päätöstä. Se nostaa
pohjakohinaa sanojen välissä, ja herkkyys on kynnys **pohjan yli**. Se
tasoittaa mikkien keskinäisen eron, ja päällekkäispuheen sääntö *vahvempi
voittaa* vertaa mikkejä toisiinsa. Käsitelty ääni on siis parempi kuunnella ja
huonompi mitata, joten kerrokset erotetaan: analyysi lukee alkuperäisen, vienti
viittaa käsiteltyyn.

### Normalisointi ennen kompressointia

Kompressorin kynnykset ovat absoluuttisia desibelejä. Käsittelemätön
podcast-mikki on helposti −40 LUFS, jolloin −12 dB:n kynnys ei ylity
kertaakaan ja koko ketju on nolla-operaatio. Siksi jokainen mikki mitataan ja
nostetaan ensin samaan äänekkyyteen.

Tavoite on **stemin** eikä ohjelman: −20 LUFS, ei −16. Yksi puhuja on äänessä
kerrallaan, joten summa osuu lähelle samaa lukemaa, ja lopullinen taso
asetetaan Final Cutissa. Ohjelman tavoitteen antaminen jokaiselle raidalle
erikseen tuottaisi summassa liian kovan.

### Näytemäärä on synkan koko lupaus

Vienti viittaa käsiteltyyn tiedostoon **samoilla ajoilla** kuin alkuperäiseen.
Yksikin lisätty tai pudotettu näyte siirtää kuvan ja äänen erilleen, eikä
virhettä huomaa ennen kuin lopputulos on koossa. Siksi pituus tarkistetaan
työprosessissa näytetaulukoista ja vielä uudestaan ffprobella, ja poikkeava
hylätään käyttämättömänä.

Tästä seuraa myös se, mitä alkuperäisestä ketjusta jätettiin ottamatta:
**mainoskatko** siirtää raitaa ja **summaus** veisi puhujien erottelun ja siten
`dialogue.<puhuja>`-roolit. Kumpikaan ei kuulu tänne.

Siirtymä mitataan erikseen ristikorrelaationa, koska pituustarkistus ei
huomaa sitä: liitännäinen voi ilmoittaa viiveensä väärin ja palauttaa oikean
mittaisen mutta kokonaan siirtyneen raidan. Korrelaatio lasketaan
verhokäyristä eikä aallonmuodosta — liitännäinen muuttaa sisältöä mutta ei
puheen rytmiä.

### Ohjaus tapahtuu resurssitasolla

Monikameraviennissä `<resources>` kopioidaan lähteestä, joten käsitelty ääni
ohjataan paikalleen vaihtamalla assetin `media-rep src`. Kulmat ja `mc-source`t
viittaavat assettiin, joten leikkauslistaan ei tarvitse koskea.

`<bookmark>` on samalla **poistettava**. Se on macOS:n tiedostoviite, joka
voittaa `src`:n: jättäminen tarkoittaisi että Final Cut avaa alkuperäisen
käsittelemättömän tiedoston kertomatta siitä mitään.

### Tilaääni on liitetty klippi, ei kulma

Kuvakulma vaihtuu joka leikkauksessa, tilaäänen on jatkuttava yli niiden.
Siksi se ei ole `mc-source` vaan `<asset-clip>` lanella −1 omalla roolillaan,
liitettynä ensimmäiseen klippiin — sama rakenne kuin littanan mikeillä.
Kameran ääni puretaan ensin ffmpegillä välimuistiin, koska soundfile ei avaa
mp4:ää.

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
