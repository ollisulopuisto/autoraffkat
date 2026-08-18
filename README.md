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

Tuetaan kahta lähdettä:

* **synkronoitu klippi** (`sync-clip`), jonka sisällä kamerat ja mikit ovat
  omilla laneillaan
* **projektin aikajana** (`project` > `sequence` > `spine`), jossa kamerat ja
  mikit on aseteltu käsin

Synkkaus luetaan XML:stä, ei lasketa. Ruutunopeus otetaan sekvenssin tai
video-assetin formaatista.

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

## Arkkitehtuuri

Analyysi on kahdessa kerroksessa, ja jako on käyttöliittymän ehto:

1. **Verhokäyrä** (`audio/envelope.py`) — ffmpeg purkaa raidan monoksi, RMS
   lasketaan 20 ms välein. Sekunteja minuuttia kohden. Ajetaan kerran
   tiedostoa kohden ja välimuistitetaan `~/Library/Caches/autoraffkat/`. Käyrä
   indeksoidaan tiedoston alusta, joten sama välimuisti kelpaa vaikka klippi
   siirtyisi aikajanalla.
2. **Päätös** (`decide.py`) — kynnykset, hystereesi, minimikestot,
   päällekkäispuhe. Ajetaan uudestaan joka säädöllä. Mitattu kahden tunnin
   aineistolla: 11–38 ms sääntökohtaisesti.

Väliin jää `analysis.py`, joka kohdistaa verhokäyrät aikajanan ruudukolle.
Kohdistus on pelkkää numpy-indeksointia, joten roolin vaihtokin on halpa.

### Miksi Python ja paikallinen web-käyttöliittymä

Analyysi on jo Pythonia ja päätöskerros on numpya. SwiftUI olisi antanut
AVFoundationin kautta toiston ja aaltomuodot, mutta olisi vaatinut joko
analyysin uudelleenkirjoituksen Swiftinä tai Python-alaprosessin ja IPC:n heti
ensimmäisestä versiosta. Tässä kokoluokassa se maksaa enemmän kuin tuo.

Myöhempi videotoisto on `<video>`-elementti proxytiedostoon, eikä se vaadi
päätöskerrokseen muutoksia: `preview.py` palauttaa jo aikajanan sekunteina, ja
`decide.py` ei tiedä käyttöliittymästä mitään.

## Ulostulo

Yksi leikkausraita spinellä. Kameroiden oma ääni pois (`srcEnable="video"`),
mikit yhtenäisinä liitettyinä klippeinä laneilla -1, -2, … omilla
`dialogue.<puhuja>`-rooleillaan. Leikkauskohdat kvantisoidaan kehyksiin niin,
ettei aikajanalle jää aukkoja eikä päällekkäisyyksiä. Kaikki aika kulkee
`Fraction`ina, koska liukulukujen pyöristysvirhe kertyy tuhansien kehysten yli.

## Rajaukset

Videon toisto, aaltomuodon piirto ja monikameraklippirakenne eivät kuulu tähän
versioon.

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
