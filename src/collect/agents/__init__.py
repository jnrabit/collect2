"""Redis-Multi-Agent-Schicht (Phase 3).

Jeder Agent ist eine dünne Klasse über transportfreier Logik: Handler bekommen
Message-Objekte, publizieren über den injizierten Bus und sind mit InMemoryBus
komplett ohne Redis testbar. Kein Handler blockiert wartend auf andere Agenten
— alles ist als Zustandsmaschine geschnitten (die Lehre aus den hängenden
Listener-Threads des Alt-Systems).
"""
