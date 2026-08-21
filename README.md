# autoraffkat

Automaattinen monikameraleikkaus. Final Cutista viety FCPXML sisään, uusi
FCPXML ulos, jossa kuva vaihtuu sen mukaan kuka puhuu. Ei renderöintiä
missään vaiheessa.

## Käyttö

```
uv run autoraffkat jakso.fcpxml
```

Selain avautuu osoitteeseen `http://127.0.0.1:8731/`.

Silmukka:

1. Synkkaa kuvat ja äänet Final Cutissa, vie XML.
2. Avaa XML sovelluksessa, nimeä raidat, säädä liukusäätimiä.
3. Vie XML (`⌘E`), tuo Final Cutiin, katso.
4. Jos ei kelpaa, takaisin kohtaan 2.

Kohdan 2 ja 3 välissä kuluu millisekunteja: liukusäätimen liike ajaa vain
päätöskerroksen, ja esikatselupalkki näyttää lopputuloksen ilman XML-kierrosta.

Vienti kirjoittaa uuden tiedoston `jakso-leikattu.fcpxml`; lähde-XML:ää ei
kosketa. Asetukset tallentuvat tiedostoon `jakso.autoraffkat.json` XML:n
viereen, joten seuraava jakso alkaa edellisen asetuksilla.

### Asennus

```
brew install ffmpeg
uv sync            # tai: pip install -e .
```

## Sisäänluku

Tuetaan kolmea lähdettä:

* **synkronoitu klippi** (`sync-clip`), jonka sisällä kamerat ja mikit ovat
omilla laneillaan
* **projektin aikajana** (`project` > `sequence` > `spine`), jossa kamerat ja
mikit on aseteltu käsin
* **monikameraklippi** (`mc-clip`), jonka kamerat ja mikit ovat kulmina

Synkkaus luetaan XML:stä, ei lasketa. Ruutunopeus otetaan sekvenssin tai
video-assetin formaatista.

### Monikamera ja osat

Pitkä nauhoitus on tavallisesti spinellä useampana monikameraklippinä — osa A,
osa B — ja jokaisessa osassa sama kamera on oma tiedostonsa. Kolme kameraa
kahdessa osassa on kuusi tiedostoa mutta kolme **raitaa**: roolit, säätimet ja
leikkaus kulkevat raidoittain, ja raita kootaan kulman nimen perusteella.

Raidan avain johdetaan tiedostonimien yhteisestä osasta
(`STATUS CAM 1 01` + `STATUS CAM 1 02` → `STATUS CAM 1`), koska kulmien nimet
ja `angleID`:t vaihtuvat viennistä toiseen mutta tiedostot eivät. Näin
tallennetut roolit kelpaavat vielä uuden viennin jälkeenkin.

## Säätimet

**Raitakohtaiset** (mikeille): herkkyys eli montako desibeliä pohjakohinan yli
puheeksi lasketaan, ja vahvistuksen korjaus. Herkkyys on kynnys pohjan
suhteen, joten se ei siirry vahvistuksen mukana; vahvistus vaikuttaa vain
mikkien keskinäiseen vertailuun päällekkäispuheessa.

**Globaalit**: lyhin kuvan kesto, ennakko (leikataan näin paljon ennen puheen
alkua), vahvistusaika (puheen on jatkuttava näin kauan ennen kuin se lasketaan),
laajan kuvan pakotusväli.

**Päällekkäispuhe**, kolme sääntöä:

* *Laaja* — molemmat äänessä, mennään laajaan
* *Pidä nykyinen* — ei leikata mihinkään
* *Vahvempi voittaa* — kovempi saa kuvan, kun ero ylittää `dominance`-rajan

Kaikkia kolmea koskee lyhin päällekkäispuheen kesto: ohikiitävä myötäily ei
laukaise sääntöä.

## Säätäminen

| Oire | Korjaus |
|---|---|
| Kuvat vaihtuvat liian usein | Nosta **lyhintä kuvan kestoa**. Jos ei riitä, nosta **vahvistusaikaa**: lyhyet äännähdykset eivät enää lasketa puheeksi. |
| Kuva vaihtuu myöhässä | Nosta **ennakkoa**. Puoli sekuntia on yleensä liikaa, 0,1–0,3 s riittää. |
| Väärä kamera hiljaisissa kohdissa | Mikki kuulee toisen puhujan vuotona. Nosta sen mikin **herkkyyttä**. |
| Toinen puhuja voittaa aina päällekkäispuheessa | Mikit ovat eri äänekkäitä. Nosta hiljaisemman **vahvistusta**. Se vaikuttaa vain mikkien keskinäiseen vertailuun, ei kynnykseen. |
| Laajaan mennään liian herkästi | Nosta **lyhintä päällekkäisyyttä**, jolloin myötäily ei laukaise sääntöä. |

## Rakenne

```
src/autoraffkat/
  timeline.py        FCPXML:n rationaaliaika (Fraction)
  model.py           mediat, roolit, asetukset, leikkaukset
  fcpxml/read.py     sync-clip, spine ja monikamera sisään
  fcpxml/write.py    uusi projekti ulos, littana tai monikamerana
  audio/envelope.py  ffmpeg + RMS, levyvälimuisti          HIDAS
  analysis.py        verhokäyrät aikajanan ruudukolle
  decide.py          kynnykset, kestot, päällekkäispuhe    NOPEA
  preview.py         palkin tiivistys selaimelle
  project.py         asetukset JSONina XML:n viereen
  server/app.py      HTTP-rajapinta
  server/static/     käyttöliittymä
```

Yksityiskohtainen perustelu: [`DESIGN.md`](DESIGN.md).

Lyhyesti: analyysi on kahdessa kerroksessa. Verhokäyrä (ffmpeg, sekunteja
minuuttia kohden) ajetaan kerran tiedostoa kohden ja välimuistitetaan
`~/Library/Caches/autoraffkat/`. Päätös (numpy, 11–38 ms kahden tunnin
aineistolla) ajetaan uudestaan joka säädöllä. Ilman tätä jakoa käyttöliittymä
olisi käyttökelvoton.

Käyttöliittymä on Python ja paikallinen web, ei SwiftUI: analyysikoodi on jo
Pythonia, eikä myöhempi videotoisto vaadi päätöskerrokseen muutoksia.

## HTTP-rajapinta

Käyttöliittymä käyttää näitä; samat kelpaavat skriptaukseen.

| | |
|---|---|
| `GET /api/state` | mediat, roolit, säätimet, verhokäyrien edistyminen |
| `POST /api/settings` | säätimet sisään, leikkauslista ja esikatselu ulos |
| `POST /api/export` | kirjoittaa leikatun XML:n, palauttaa polun |
| `POST /api/reload` | lukee lähde-XML:n uudestaan levyltä |

```
curl -s -X POST localhost:8731/api/export \
     -H 'Content-Type: application/json' -d @asetukset.json
```

`POST /api/settings` palauttaa `ok: false` ja luettavan `problems`-listan, kun
roolit ovat kesken. Se on normaali välitila, ei virhe.

## Kun jokin ei toimi

| Viesti tai oire | Syy |
|---|---|
| `ffmpeg puuttuu polusta` | `brew install ffmpeg` |
| `Tiedostoa ei löydy levyltä` raitalistassa | XML viittaa polkuun jota ei ole: materiaali on siirtynyt viennin jälkeen tai vienti osoittaa proxyihin. Yhdistä media Final Cutissa ja vie uudestaan. |
| `XML:stä ei löytynyt projektia eikä synkronoitua klippiä` | Viety on esimerkiksi pelkkä event. Valitse synkattu klippi tai projekti ennen vientiä. |
| `Laajalla kuvalla ja mikeillä ei ole yhteistä aikaa` | Roolitus osoittaa medioihin jotka eivät ole päällekkäin aikajanalla. |
| Palkki näyttää oikealta, Final Cut ei | Tarkista sekvenssin ruutunopeus. Se luetaan XML:stä, joten väärä arvo on lähteessä. |
| Verhokäyrät lasketaan aina uudestaan | Välimuistin avaimessa on muokkausaika. Verkkolevy joka muuttaa aikaleimoja ei osu välimuistiin. |

## Ulostulo

Yksi leikkausraita spinellä. Kameroiden oma ääni pois (`srcEnable="video"`),
mikit yhtenäisinä liitettyinä klippeinä laneilla -1, -2, … omilla
`dialogue.<puhuja>`-rooleillaan. Leikkauskohdat kvantisoidaan kehyksiin niin,
ettei aikajanalle jää aukkoja eikä päällekkäisyyksiä. Kaikki aika kulkee
`Fraction`ina, koska liukulukujen pyöristysvirhe kertyy tuhansien kehysten yli.

## Rajaukset

Videon toisto ja aaltomuodon piirto eivät kuulu tähän versioon.

Monikameralähteestä ulos tulee monikameraleikkaus, tavallisesta lähteestä
littana leikkaus. Kummankin muodon valinta tapahtuu lähteen mukaan, eikä
kesken leikkauksen voi vaihtaa.

## Testit

```
uv run pytest
```

Testiaineisto syntetisoidaan ffmpegillä: siniaaltopurskeita tunnetuissa
kohdissa, joten päätöksen oikeellisuus on tarkistettavissa ilman oikeaa
kuvausmateriaalia (`tests/make_fixture.py`).

## Prototyyppi

`prototype/autocut_multicam.py` on alkuperäinen komentorivityökalu, josta tämä
lähti liikkeelle. Säilytetään viitteenä.
