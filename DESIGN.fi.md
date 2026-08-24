# Rakenne

*[In English](DESIGN.md)*

> Tämä on suomenkielinen versio. Ensisijainen dokumentaatio on
> [`DESIGN.md`](DESIGN.md), ja se päivittyy ensin.

Tämä kuvaa miksi koodi on jaettu niin kuin se on. `README.fi.md` kertoo miten
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
2. **1/f-tempo-ohjaus.** `_compute_tempo` laskee vuorottelunopeuden liukuvassa
   45 s ikkunassa ja säätää efektiivistä vähimmäiskestoa $\tau$ nopean dialogin
   ja rauhallisen puheen välillä.
3. **Yksi äänessä** → hänen lähikuvansa.
4. **Useampi äänessä.** Jos päällekkäisyys on lyhyempi kuin `min_overlap`, se
   ei ole päällekkäispuhetta vaan myötäilyä: valitaan kovempi eikä laukaista
   sääntöä. Muuten sovelletaan valittua sääntöä.
5. **Puhuja ilman lähikuvaa** → laaja. **Lähikuva jota ei ole aikajanalla
   tässä kohtaa** → pidä nykyinen.
6. **Kestorajoitukset ja leikkausrytmi (J-cut / L-cut)** jaksosilmukassa:
   `lead` (J-cut) siirtää leikkausta ennen puheen alkua (hengähdykset ja ilmeet),
   `hang` (L-cut) pitää edellisen puhujan taukojen yli. Lyhin kuvan kesto estää
   leikkausta osumasta liian lähelle edellistä. Jos molemmat yhdessä työntävät
   leikkauksen jakson yli, leikkausta ei tehdä.
7. **Pitkä puheenvuoro ja hengähdystaukohaku** (`_force_wide` + `_find_breath_point`).

#### Kumpi voittaa, ennakko vai häntä

Ne ovat saman leikkauskohdan kaksi reunaa. `lead` vetää leikkausta
aikaisemmaksi, tulevan äänen edelle; `hang` on lattia, joka pitää edellisen
puhujan kasvot kuvassa vielä hänen oman puheensa loputtua. Leikkaus osuu
myöhempään näistä, joten tauon pituus ratkaisee: pitkän tauon jälkeen ennakko
voittaa ja leikkaus ennakoi seuraavaa puhujaa (J-cut), nopeassa
vuoronvaihdossa häntä voittaa ja vanhat kasvot jäävät uuden äänen päälle
(L-cut). Lähetysoletuksilla raja on noin 0,9 sekunnin tauko, mikä on pidempi
kuin tavallinen puheenvuorojen väli — eli useimmat vaihdot ovat L-cutteja ja
vain todelliset tauot saavat ennakon.

Lattia pätee vain kun väistyvä puhuja on todella vaiennut. Päällekkäispuheessa
hän on yhä äänessä, leikkaus ei johdu siitä että hän lopetti, eikä hännälle ole
paikkaa: päällekkäispuheen sääntö leikkaa ajallaan. Sama koskee ohjelman
ensimmäistä leikkausta, jossa väistyvä kuva on laaja eikä siinä ole kasvoja
joihin viivähtää.

Häntää lyhyempi vastaus ei saa kuvaa lainkaan — lattia työntää leikkauksen sen
puhujan jakson yli, ja kuva jää siihen missä on. Se on sama mekanismi kuin
vahvistusaika, toisesta päästä.

### Pitkä puheenvuoro ja reaktiokuvat

Kohdat 1–6 tuottavat oikean kuvan mutta eivät rytmiä: yksinpuhelu antaa yhden
lähikuvan niin pitkäksi kuin puhe kestää, ja katsojalle se on minuutti samaa
kasvokuvaa. Kun sama puhuja on pitänyt lattiaa `wide_every` sekuntia, kuva
katkeaa luontevaan taukoon tai hengähdykseen.

Jatkoja on kolme, koska ne ovat eri asia leikkauksellisesti eikä mikään ole
aina oikein:
* **Palaa puhujaan** antaa laajan kestää `wide_hold` ja palaa samaan
  kuvaan; rytmi pysyy puhujassa, ja se sopii keskustelulle jossa monologi on
  poikkeus.
* **Jää laajaan** pitää laajan seuraavaan puheenvuoroon asti; pitkä
  yksinpuhelu näyttää tilanteelta eikä kasvokuvalta, ja leikkauksia tulee
  selvästi vähemmän.
* **Reaktiokuva** leikkaa toisen puhujan lähikuvaan `wide_hold`-ajaksi, sitten
  takaisin. (Palaa laajaan, jos toista lähikuvaa ei ole).

Valinta on makua, joten se on säädin eikä vakio.

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

Ryhmittely tapahtuu kulman nimellä (`"1"`, `"host a Track2"`), koska se on
leikkaajan oma merkintä siitä että kyse on samasta kamerasta. Avain sen sijaan
johdetaan tiedostonimistä, koska nimet ja `angleID`:t vaihtuvat viennistä
toiseen. Kahta saman multicamin kulmaa ei koskaan yhdistetä, vaikka nimet
normalisoituisivat samoiksi.

## Kirjoitus

Yksi spine, yksi klippi per kuva. Kameroiden oma ääni pois
`srcEnable="video"`. Mikit liitettyinä klippeinä ensimmäiseen spine-klippiin
laneilla −1, −2, … rooleilla `dialogue.<puhuja>`.

### Säätimet kulkevat leikkauksen mukana

Viennin nimessä on rytmiprofiili ja jokainen säädin joka poikkeaa
oletuksestaan (`jakso-cut custom 3s louder stay audio.fcpxml`). Se ei ole
koriste: Final Cutin selaimessa tiedostonimi on ainoa mikä erottaa leikkaukset
toisistaan, ja silmukka tuottaa niitä jaksoa kohti useita. `pick` tuntee omat
sanansa eikä muita, joten vieras `-cut`-loppuinen nimi kelpaa yhä lähteeksi.

Koko asetusjoukko menee tiedoston sisään. DTD sanoo
`sequence (note?, spine, metadata?)`, joten molemmille on paikka ja järjestys
on osa sääntöä: `<note>` on käännetty yhden rivin tiivistelmä — versio, rytmi,
kuvien kestot, säännöt ja se käsiteltiinkö mikit — ja `<metadata>`-lohkossa on
oma `<md>` jokaiselle säätimelle sekä koko asetusjoukko JSONina avaimella
`fi.autoraffkat.settings`. Käänteinen nimiavaruus on Applen tapa ja pitää
avaimet erossa Final Cutin omista. XML kulkee koneelta toiselle,
asetustiedosto ei välttämättä kulje sen mukana.

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

### Liitännäisen säätimet ovat liitännäisen omissa yksiköissä

Ketjussa ei ole omaa kohinanvaimennusta — se on ulkoisen liitännäisen työ, ja
liitännäinen ilman säätimiään on se preset joka sattui olemaan tehdasoletus.
`AudioSettings.plugin_params` on `{nimi: arvo}` -kartta, ja arvo on
liitännäisen omassa yksikössä (`plugin.input_gain = 3.0` on kolme desibeliä)
eikä se 0–1-raaka-arvo jota formaatti alla käyttää. Syitä on kaksi: muunnos
raa'asta näkyvään ei ole aina lineaarinen ja pedalboard osaa sen valmiiksi, ja
desibeliluku on luettava asetustiedostossa ja viedyn XML:n metatiedossa, joissa
`0,5625` ei kertoisi kenellekään mitään.

Tallennetaan vain kosketut säätimet; muut jäävät liitännäisen oletuksiin, eikä
asetustiedosto täyty arvoista joita kukaan ei ole valinnut.

Kaksi sääntöä estää väärän arvon päätymisen liitännäiselle huomaamatta. Nimi
tarkistetaan `plugin.parameters`-sanakirjasta ennen kirjoitusta, koska
pedalboardin liitännäisolio ottaa vastaan *minkä tahansa* attribuutin —
tuntematon nimi näyttäisi menneen perille eikä vaikuttaisi mihinkään. Ja
tuntematon tai rajojen ulkopuolinen nimi ohitetaan eikä kaadeta: asetukset
periytyvät edellisestä jaksosta, jonka liitännäinen on voinut olla toinen, ja
oikea käytös on silloin ajaa liitännäinen omilla oletuksillaan.

Säätimien luettelointi lataa liitännäisen, mikä kestää sekunteja, joten se on
oma pyyntönsä (`/api/plugin-params`) eikä osa liitännäisluetteloa — siinä on
satoja rivejä. Tulos jää polun mukaan välimuistiin: liitännäinen ei muutu
ohjelman ajon aikana.

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

### Vaimennus käyttää kuvan puheentunnistusta

Mikin portti on klassisesti vaikea: tunnistus välkkyy tavuvälien yli ja
reagoi yskäisyyn. Tässä tunnistus on kuitenkin jo olemassa, säädetty
herkkyyssäätimillä ja katsottu esikatselupalkista — sama `SpeakerLanes.on`
joka päättää kuvan. Portti saa siis ohjauksen ilmaiseksi.

Kaksi asiaa piti silti lisätä.

**Kovin voittaa.** Pelkkä kynnys ei erota puhujia: kaksi mikkiä samassa
huoneessa kuulevat molemmat, ja mitatussa aineistossa kumpikin ylitti
kynnyksen 41 % ajasta yhtä aikaa. Vuoto on kuitenkin selvästi hiljempaa —
mediaaniero 12,8 dB — joten auki jätetään vain kovin mikki ja ne jotka ovat
`duck_dominance_db`:n sisällä siitä. Kuudella desibelillä päällekkäisyys
putosi 41 %:sta 6 %:iin.

**Vaimennus vain peittävän äänen alla.** Ensimmäinen versio vaimensi aina kun
puhuja oli hiljaa, ja se kuulosti kamalalta: 20 millisekunnin kuoppia, 13–33
vaimennusta minuutissa, ja liu'ut keskellä hiljaisuutta. Portti kuuluu aina
kun mikään ei peitä sitä.

Nyt vaimennus voi olla olemassa vain silloin kun **jokin toinen mikki on
auki**. Lasku ajoitetaan toisen puheen alkuun ilman ennakkoa — lasku ei saa
alkaa ennen kuin peittävä ääni on tullut — ja peittävän jakson lopusta
leikataan pito ja paluun mitta pois, jotta myös nousu tapahtuu peittävän
äänen alla. Mitattuna hiljaisuudessa tapahtuva vaimennus putosi 5,4 %:sta
1,6 %:iin ja 12,7 %:sta 5,2 %:iin, ja loppu on lauseensisäisiä taukoja joissa
toinen puhuja on selvästi vielä kesken.

Syvyys on säädin mutta melkein merkityksetön: vuoto on jo ~13 dB puheen alla,
joten −9 dB ja −15 dB eroavat yhteissummassa alle 0,1 dB ja erotussignaali on
keskimäärin 34 dB miksin alapuolella. Tämä myös selittää miksi ensimmäinen
versio kuulosti pahalta vaikka taso tuskin liikkui: kuultiin artefaktit, ei
vaimennusta. Oletus on siksi matala −9 dB, joka tekee vähiten vahinkoa jos
tunnistus erehtyy.

Liu'ut ovat desibeleissä eivätkä amplitudissa, koska kuulo on logaritminen:
lineaarinen liuku on puolivälissä jo lähes perillä ja kuulostaa äkkinäiseltä.
Ne ovat myös epäsymmetriset ja hitaat — 0,25 s alas, 0,4 s ylös — koska
piilossa oleva liuku ei hyödy nopeudesta.

**Omat ajat.** Kuva odottaa vahvistusaikaa ennen leikkausta; portin on
avauduttava heti. `open_windows` pudottaa liian lyhyet jaksot (`min_open`,
yskäisy), avaa etukäteen (`lookahead`) ja pitää auki jälkikäteen (`hold`).
Ennakko on mahdollinen vain koska käsittely on jälkikäteistä — juuri sen
puuttuminen tekee reaaliaikaisesta portista sanoja syövän.

`_close_gaps` oli tähän asti kuollutta koodia `decide.py`:ssä. Sanavälien
täyttö tapahtuu nyt implisiittisesti: ennakko ja pito laajentavat jaksoja
molempiin suuntiin, ja lähekkäiset sulautuvat.

Vaimennus tehdään näytetasolla jaksoittain eikä koko tiedoston mittaisella
vahvistuskäyrällä: tunnin mikki on 184 miljoonaa näytettä, ja float-taulukko
sen päälle olisi kolme neljäsosaa gigatavusta. Jaksoja on tuhansia.

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
