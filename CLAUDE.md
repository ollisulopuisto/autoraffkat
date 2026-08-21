# autoraffkat

FCPXML sisään, FCPXML ulos. Kuva vaihtuu puhujan mukaan. Ei renderöintiä.

## Kaksi kerrosta, älä sekoita

`audio/envelope.py` on hidas (ffmpeg, sekunteja) ja välimuistitettu levylle.
`decide.py` on nopea (numpy, millisekunteja) ja ajetaan joka säädöllä. Mitään
tiedostojen lukua ei saa valua `decide.py`:hen eikä sinne kutsuttavaan
`analysis.build_grid`:iin — se rikkoo käyttöliittymän vasteajan, joka on
toimeksiannon tärkein yksittäinen vaatimus.

`decide.py` ei myöskään saa silmukoida yksittäisten näytteiden yli. Silmukat
kulkevat jaksojen (`_runs`) yli, joita on tuhansia, ei näytteiden, joita on
satojatuhansia.

## Aika on Fraction

Kaikki XML:stä luettu ja XML:ään kirjoitettu aika kulkee `timeline.py`:n läpi
`Fraction`ina. Liukuluku kelpaa vain analyysikerroksessa. Syy: pyöristysvirhe
kertyy tuhansien kehysten yli ja aikajanalle jää aukkoja.

FCPXML:n aikasemantiikka: klipin `offset` on isännän paikallisessa aikapohjassa,
jonka nollakohta on isännän `start`. Lapsen absoluuttinen paikka on siis
`isännän_absoluuttinen + (lapsen_offset - isännän_start)`. Tämä koskee sekä
liitettyjä klippejä että sync-clipin sisältöä, ja se on `fcpxml/read.py`:n
`_walk`-funktion koko idea.

Monikamerassa lisäksi: kulman sisältö on rajattava `mc-clip`:n kestoon
(`_walk`:n `bounds`), koska kulma ulottuu koko multicamin yli ja sama multicam
voi olla spinellä kahdesti.

## Raita ei ole media

Roolituksen yksikkö on `Timeline.tracks`, ei `Timeline.media`. Monikamerassa
sama kulma on eri tiedosto joka osassa mutta yksi raita. Kaikki mikä lukee
rooleja, säätimiä tai `Segment.angle`ia puhuu raita-avaimista. Ilman tätä
`Roles.wide_key` ja `closes` olisivat listoja ja jokainen niitä lukeva kohta
joutuisi käsittelemään monta avainta.

## Roolit peritään jaksosta toiseen

Uusi jakso ilman omia asetuksia lukee lähimmän aiemman
`*.autoraffkat.json`-tiedoston ja ottaa siitä täsmäävien raita-avainten
roolit. Tämä on koko syy siihen, että raita-avain johdetaan tiedostonimestä
eikä kulman nimestä tai `angleID`:stä: sarjassa kamerat pysyvät, kulmanumerot
eivät. Jos avaimen johtamista muuttaa, perintä lakkaa toimimasta hiljaisesti.

## Herkkyys ja vahvistus eivät ole sama asia

Herkkyys on kynnys pohjakohinan yli, joten vahvistus ei siirrä sitä — pohja
siirtyy saman verran. Vahvistus vaikuttaa vain mikkien keskinäiseen vertailuun
päällekkäispuheessa. Jos tämän muuttaa, säätimet alkavat vaikuttaa toisiinsa.

## Ääni: analysoi raaka, vie käsitelty

`audio/mix.py` on kolmas hidas kerros. Kaksi asiaa eivät ole neuvoteltavissa:

Alkuperäisen päälle ei kirjoiteta. Verhokäyrän välimuisti avainnetaan
muokkausajalla, joten päällekirjoitus laskisi käyrän uudestaan — ja uusi
laskenta osuisi käsiteltyyn ääneen. Analyysi tehdään aina raa'asta: kompressori
nostaa pohjakohinaa sanojen välissä ja tasoittaa mikkien eron, eli tuhoaa
tasan ne kaksi asiaa joihin herkkyys ja päällekkäispuheen sääntö nojaavat.

Näytemäärä ei saa muuttua. Vienti viittaa käsiteltyyn tiedostoon samoilla
ajoilla kuin alkuperäiseen. Tarkistus on kahdessa paikassa, ja poikkeava
hylätään — käsittely tapahtuu vieraassa ympäristössä eikä sen lupauksiin
nojata.

Kun assetin `src` ohjataan toisaalle, `<bookmark>` on poistettava. Se on
macOS:n tiedostoviite joka voittaa `src`:n, ja jättäminen tarkoittaisi että
Final Cut avaa käsittelemättömän tiedoston kertomatta siitä mitään.

Kanavanauha on `audio/chain.py`:ssä, pedalboardilla. Kaksi kohtaa joissa
kirjasto ei tee mitä nimi lupaa, molemmat mitattuja:

* `plugin.process(..., reset=False)` **lyhentää** tulosta liitännäisen viiveen
  verran (dxRevivella 4641 näytettä). Käytä aina `reset=True`, äläkä käsittele
  tiedostoa paloissa.
* `pedalboard.Limiter` tekee makeup-vahvistuksen: se nosti −20 LUFS:n
  −15,8:aan ja huiput nollaan. Tilalla on `peak_guard`, staattinen vaimennus
  joka ei koskaan nosta.

## Mikki kulmaan, tilaääni lanelle — ja miksi

Mikkiääni menee vientiin monikameraklipin sisään (`mc-source`), joten se ei
voi irrota synkasta vaikka käyttäjä leikkaisi Final Cutissa miten tahansa.
Tilaääni on liitetty klippi, koska `mc-source` ei tunne tasoa — ja siksi se
**voi** irrota rippausleikkauksessa. Jos tilaäänelle joskus keksii tavan
mennä kulmaksi tasoineen, se on parannus.

## Final Cut on ankarampi kuin oma lukija

Vienti on tarkistettava Final Cutin omaa DTD:tä vasten
(`/Applications/Final Cut Pro.app/.../Interchange.framework/.../FCPXMLv1_*.dtd`,
`xmllint --dtdvalid`). Oma lukija hyväksyy paljon enemmän kuin tuonti: kerran
`mc-clip`iin kirjoitettiin `tcFormat`, joka kelpasi lukijalle mutta kaatoi
koko tuonnin. `clip` ja `asset-clip` tuntevat sen, `mc-clip` ei.

Johdetut tiedostot eivät mene `.fcpxmld`-paketin sisään vaan sen viereen ja
saavat paketin nimen. Paketti kuuluu Final Cutille.

## Testit

`tests/make_fixture.py` syntetisoi aineiston ffmpegillä: siniaaltopurskeet
tunnetuissa kohdissa (`SPEECH_A`, `SPEECH_B`). Projektifixture alkaa lähteen
sekunnista 1, synkkaklippi nollasta — vertailussa on käytettävä
`source_to_timeline`-muunnosta, ei raakoja lukuja.

`multicam.fcpxml` on sama aineisto kahtena osana: osien tiedostot ovat
kopioita, koska ryhmittely katsoo tiedostonimeä eikä sisältöä. Siinä
aikajanan hetki vastaa tiedoston hetkeä, joten `source_to_timeline` antaa
identiteetin — eri kuin projektifixturessa.

Asetukset kirjoitetaan XML:n viereen, joten testit jotka vievät tai tallentavat
tarvitsevat `scratch_xml`-fixturen, eivät jaettua `fixture_dir`iä.
