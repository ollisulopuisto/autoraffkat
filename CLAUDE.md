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

## Herkkyys ja vahvistus eivät ole sama asia

Herkkyys on kynnys pohjakohinan yli, joten vahvistus ei siirrä sitä — pohja
siirtyy saman verran. Vahvistus vaikuttaa vain mikkien keskinäiseen vertailuun
päällekkäispuheessa. Jos tämän muuttaa, säätimet alkavat vaikuttaa toisiinsa.

## Testit

`tests/make_fixture.py` syntetisoi aineiston ffmpegillä: siniaaltopurskeet
tunnetuissa kohdissa (`SPEECH_A`, `SPEECH_B`). Projektifixture alkaa lähteen
sekunnista 1, synkkaklippi nollasta — vertailussa on käytettävä
`source_to_timeline`-muunnosta, ei raakoja lukuja.

Asetukset kirjoitetaan XML:n viereen, joten testit jotka vievät tai tallentavat
tarvitsevat `scratch_xml`-fixturen, eivät jaettua `fixture_dir`iä.
