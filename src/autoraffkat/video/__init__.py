"""Kuvan analyysi: kolmas hidas kerros.

Sama työnjako kuin äänellä. ``measure.py`` purkaa ruutuja ja mittaa niistä
lukuja — sekunteja tiedostoa kohden, joten tulos välimuistitetaan levylle.
``autoraffkat.reactions`` lukee valmiin taulukon ja päättää siitä millisekunneissa,
eikä se saa avata yhtään tiedostoa.

Raja on tässä tahallaan: **tunnistin on se osa jonka odotetaan vaihtuvan.**
Siksi välimuistiin talletetaan mittaukset eikä pisteitä. Painojen säätäminen
on silloin ilmaista, ja tunnistimen vaihtaminen mitätöi välimuistin itsestään,
koska sen nimi ja versio ovat avaimessa.
"""
