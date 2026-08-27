"""Kuka istuu missä, ja kuinka paljon sitä saa kuulla.

Kaksi asiaa, jotka kuuluvat yhteen: **istumajärjestys** mitataan kuvasta, ja
**panorointi** johdetaan siitä. Kumpikin on nopeaa laskentaa valmiiden
mittausten päällä — ei tiedostojen lukemista — joten tämä saa olla
säätökierroksessa mukana, samalla säännöllä kuin ``decide.py`` ja
``reactions.py``.

Panorointi ei kuulu tiedostoihin. Se on Final Cutin ``adjust-panner``,
koska se on tasopäätös siinä missä vaimennuskin: leikkaaja saa muuttaa sen
jälkikäteen ilman että mitään ajetaan uudestaan. Sama peruste kuin
reaktiokuvien omalla lanella.

Panorointi on myös **hyvin** hienovarainen. Kuulokkeilla kuunneltuna sitä ei
juuri huomaa, ja se on tarkoituskin: puhe kuuluu keskeltä, ja leveä
panorointi tekee kahden puhujan keskustelusta radiokuunnelman. Muutama
prosentti riittää antamaan kuvalle ja äänelle saman maantieteen.
"""

from __future__ import annotations

import numpy as np

# Kuinka leveälle puhujat levitetään, prosentteina Final Cutin asteikolla
# (-100 = vasen, +100 = oikea). Ensimmäinen luku on kahdelle puhujalle.
#
# Nämä eivät ole mitattuja lukuja vaan valittu yläraja: mitattavaa olisi
# «kuuluuko tämä», ja siihen vastaus on että ei juuri pidäkään. Leveys
# kasvaa puhujamäärän mukana vain sen verran että paikat pysyvät erillään.
PAN_WIDTH = {2: 6.0, 3: 8.0, 4: 10.0, 5: 12.0}

# Useampaa kuin viittä ei panoroida. Kuudella paikat ovat niin lähellä
# toisiaan ettei ero ole enää paikka vaan epätarkkuus, ja silloin keskeltä
# on parempi kuin melkein keskeltä.
PAN_MAX_SPEAKERS = 5

# Kuinka monta mittausta puhujalta tarvitaan ennen kuin puolta uskotaan.
# Yksittäinen ruutu voi olla mitä tahansa; sata on jo jakauma.
SIDE_MIN_FRAMES = 100


def side(table: dict) -> float:
    """Puhujan paikka vasen–oikea, välillä -1…+1, tai ``nan``.

    Mitta on ``turn``: nenän sijainti silmien puolivälin suhteen, jaettuna
    silmien etäisyydellä. Kaksi vastakkain istuvaa katsoo toisiaan, joten
    **vasemmalla istuva katsoo oikealle** ja hänen nenänsä on kuvassa
    silmiensä oikealla puolella — ``turn`` on positiivinen.

    Merkki on siis päinvastoin kuin arvaus «nenä osoittaa sinne missä
    istutaan», ja juuri siksi se on tarkistettu kuvista eikä päätelty:
    oikealla jaksolla vasemmalla istuvan mediaani oli +0,46 ja oikealla
    istuvan -0,28, molemmissa osissa sama.

    Kehystys (``cx``) ei kelpaa tähän. Samalla jaksolla mitattuna molemmat
    olivat kuvan oikealla puoliskolla, +0,51 ja +0,60, eli kehystys kertoo
    kuvaajan tavoista eikä istumajärjestyksestä.
    """
    found = np.asarray(table.get("found", []), dtype=bool)
    if found.sum() < SIDE_MIN_FRAMES:
        return float("nan")
    turn = np.asarray(table["turn"], dtype=np.float64)[found]
    turn = turn[np.isfinite(turn)]
    if turn.size < SIDE_MIN_FRAMES:
        return float("nan")
    # Mediaani eikä keskiarvo: yksittäinen väärin osunut maamerkki voi olla
    # kaukana, eikä sen pidä siirtää koko puhujaa.
    return float(np.clip(np.median(turn), -1.0, 1.0))


def order(sides: dict) -> list:
    """Puhujat vasemmalta oikealle. Mittaamattomat viimeisiksi, nimen mukaan.

    Järjestys on ``-turn``: positiivinen ``turn`` on vasemmalla, ja
    vasemmalta oikealle luettuna se tarkoittaa laskevaa ``turn``ia.
    """
    known = sorted((n for n in sides if np.isfinite(sides[n])),
                   key=lambda n: -sides[n])
    unknown = sorted(n for n in sides if not np.isfinite(sides[n]))
    return known + unknown


def pans(sides: dict) -> dict:
    """Puhuja -> panorointi Final Cutin asteikolla, -100…+100.

    Paikat jaetaan tasan mitatun järjestyksen mukaan, ei mitatun kulman
    suhteessa: kulma kertoo **järjestyksen** luotettavasti mutta etäisyyden
    ei lainkaan — se riippuu tuolien asennosta ja objektiivista. Kolme
    puhujaa on siis vasen, keskus, oikea, ei «kaksi lähes samassa».

    Kaikki mittaamattomat jäävät keskelle: paikkaa jota ei tiedetä ei
    arvata, ja keskus on ainoa arvo joka ei ole koskaan väärin.
    """
    names = order(sides)
    measured = [n for n in names if np.isfinite(sides.get(n, float("nan")))]
    out = {name: 0.0 for name in sides}
    count = len(measured)
    if count < 2 or count > PAN_MAX_SPEAKERS:
        return out
    width = PAN_WIDTH[count]
    # Tasavälit -width/2 … +width/2. Parittomalla määrällä keskimmäinen
    # osuu nollaan itsestään, mikä on juuri haluttu: kolmesta yksi on
    # keskellä eikä ketään siirretä turhaan.
    steps = np.linspace(-width / 2.0, width / 2.0, count)
    for name, value in zip(measured, steps):
        out[name] = round(float(value), 2)
    return out
