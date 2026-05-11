# SPEC — GNSS-RTK-bridge

## Objectif

Créer un service Python qui :
- lit les trames NMEA d’un récepteur GNSS UM982/WTRTK via port série ;
- parse les trames GGA et RMC avec `pynmeagps` ;
- maintient le dernier état GNSS en mémoire ;
- diffuse les coordonnées, vitesse et cap en UDP localhost avec Protobuf ;
- gère un client NTRIP dans un second thread ;
- envoie périodiquement la dernière GGA au caster NTRIP ;
- reçoit les corrections RTCM ;
- renvoie les corrections RTCM au récepteur GNSS via le même port série.

---

## Dépendances

```bash
pip install pyserial pynmeagps protobuf pygnssutils pyrtcm
```

NTRIP doit être géré avec `GNSSNTRIPClient` fourni par `pygnssutils`.

---

## Architecture générale

```text
/dev/ttyUSB0
   │
   ├── GnssThread
   │      ├── lit le port série
   │      ├── parse GGA/RMC avec pynmeagps
   │      ├── met à jour SharedGnssState
   │      └── publie GnssFix en UDP protobuf
   │
   └── NtripClientThread
          ├── se connecte au caster NTRIP
          ├── envoie la dernière GGA
          ├── reçoit les corrections RTCM
          └── écrit les RTCM vers le GNSS
```

---

## Protobuf

Créer `gnss_fix.proto` :

```proto
syntax = "proto3";

package natuition.gnss;

message GnssFix {
  uint64 timestamp_monotonic_ns = 1;

  bool valid = 2;

  double latitude_deg = 3;
  double longitude_deg = 4;
  double altitude_m = 5;

  float speed_mps = 6;
  float course_deg = 7;

  uint32 fix_quality = 8;
  uint32 satellites = 9;
}
```

Génération Python :

```bash
protoc --python_out=. gnss_fix.proto
```

---

## Configuration INI

Créer `config.ini` :

```ini
[serial]
port = /dev/ttyUSB0
baudrate = 115200
timeout_s = 0.1

[udp]
host = 127.0.0.1
port = 5010

[ntrip]
enabled = true
host = caster.centipede.fr
port = 2101
mountpoint = MOUNTPOINT
username = USER
password = PASSWORD
gga_interval_s = 5.0
```

Chargement avec `configparser` :

```python
import configparser

config = configparser.ConfigParser()
config.read("config.ini")

serial_port = config["serial"]["port"]
baudrate = int(config["serial"]["baudrate"])
timeout_s = float(config["serial"]["timeout_s"])
```

---

## Classe GnssState

Créer une dataclass interne :

```python
@dataclass
class GnssState:
    timestamp_monotonic_ns: int

    valid: bool

    latitude_deg: float | None
    longitude_deg: float | None
    altitude_m: float | None

    speed_mps: float | None
    course_deg: float | None

    fix_quality: int | None
    satellites: int | None

    last_gga_sentence: bytes | None
```

`last_gga_sentence` est uniquement interne.  
Elle ne doit pas être diffusée dans le protobuf.

---

## Classe SharedGnssState

Objet thread-safe partagé entre les threads.

Responsabilités :
- stocker le dernier état GNSS ;
- protéger les accès avec `threading.RLock`;
- fournir des snapshots propres ;
- stocker la dernière GGA brute pour NTRIP.

Méthodes attendues :

```python
class SharedGnssState:
    def update_from_gga(self, msg, raw_line: bytes) -> None:
        ...

    def update_from_rmc(self, msg) -> None:
        ...

    def snapshot(self) -> GnssState:
        ...

    def get_last_gga(self) -> bytes | None:
        ...
```

---

## Classe GnssThread

Classe héritant de `threading.Thread`.

Responsabilités :
- ouvrir le port série avec `pyserial`;
- lire en continu les trames NMEA ;
- parser avec `pynmeagps.NMEAReader`;
- traiter uniquement les messages `GGA` et `RMC`;
- ignorer les autres messages ;
- mettre à jour `SharedGnssState`;
- publier en UDP Protobuf ;
- fournir une méthode d’écriture RTCM vers le GNSS.

Signature souhaitée :

```python
class GnssThread(threading.Thread):
    def __init__(
        self,
        serial_port: str,
        baudrate: int,
        state: SharedGnssState,
        publisher: UdpProtobufPublisher,
        serial_lock: threading.Lock,
        timeout_s: float = 0.1,
    ):
        ...
```

Méthodes attendues :

```python
def run(self) -> None:
    ...

def stop(self) -> None:
    ...

def write_rtcm(self, data: bytes) -> None:
    ...
```

Écriture RTCM :

```python
def write_rtcm(self, data: bytes) -> None:
    with self.serial_lock:
        self.serial.write(data)
```

---

## Parsing NMEA avec pynmeagps

Principe :

```python
from pynmeagps import NMEAReader

reader = NMEAReader(serial_stream)

raw_data, parsed = reader.read()
```

Traitement :

```python
if parsed.msgID == "GGA":
    state.update_from_gga(parsed, raw_data)

elif parsed.msgID == "RMC":
    state.update_from_rmc(parsed)
```

Talkers acceptés :
- `GP`
- `GN`

Messages acceptés :
- `GGA`
- `RMC`

Champs à extraire depuis GGA :
- latitude
- longitude
- altitude
- fix_quality
- satellites
- dernière trame GGA brute

Champs à extraire depuis RMC :
- validité
- vitesse
- course over ground

Conversion vitesse :

```python
speed_mps = speed_knots * 0.514444
```

Attention :
- `course_deg` est un cap de déplacement GNSS ;
- ce n’est pas une orientation robot fiable à l’arrêt.

---

## Classe UdpProtobufPublisher

Responsabilités :
- convertir `GnssState` en `GnssFix`;
- envoyer le message en UDP localhost ;
- ne jamais bloquer durablement.

Signature souhaitée :

```python
class UdpProtobufPublisher:
    def __init__(self, host: str, port: int):
        ...

    def publish(self, state: GnssState) -> None:
        ...
```

Destination par défaut :

```text
127.0.0.1:5010
```

Comportement :
- UDP sans retry ;
- perte de paquet acceptable ;
- les clients utilisent toujours le dernier paquet reçu.

---

## Classe NtripClientThread

Classe héritant de `threading.Thread`.

Responsabilités :
- utiliser `GNSSNTRIPClient` de `pygnssutils` pour gérer le client NTRIP ;
- se connecter au caster NTRIP ;
- envoyer périodiquement la dernière GGA au caster ;
- recevoir le flux RTCM binaire ;
- écrire immédiatement les corrections RTCM vers le GNSS via `gnss_write_rtcm`.

Signature souhaitée :

```python
class NtripClientThread(threading.Thread):
    def __init__(
        self,
        host: str,
        port: int,
        mountpoint: str,
        username: str,
        password: str,
        state: SharedGnssState,
        gnss_write_rtcm: Callable[[bytes], None],
        gga_interval_s: float = 5.0,
    ):
        ...
```

Méthodes attendues :

```python
def run(self) -> None:
    ...

def stop(self) -> None:
    ...
```

Implémentation attendue :
- s’appuyer sur `GNSSNTRIPClient` plutôt que réimplémenter le protocole NTRIP à la main ;
- fournir à `GNSSNTRIPClient` les paramètres caster, mountpoint, identifiants et GGA ;
- récupérer les bytes RTCM reçus ;
- les transmettre au GPS avec `gnss_write_rtcm(rtcm_data)`.

---

## Gestion NTRIP avec GNSSNTRIPClient

Le client NTRIP ne doit pas être réimplémenté manuellement sauf nécessité.

Utiliser :

```python
from pygnssutils import GNSSNTRIPClient
```

Le thread `NtripClientThread` doit :
- initialiser un `GNSSNTRIPClient` ;
- configurer le caster, le port, le mountpoint, le username et le password ;
- fournir régulièrement la dernière trame GGA issue de `SharedGnssState.get_last_gga()` ;
- récupérer les corrections RTCM reçues ;
- écrire les corrections dans le récepteur GNSS avec `gnss_write_rtcm(rtcm_data)`.

Erreurs à gérer :
- authentification incorrecte ;
- mountpoint invalide ;
- perte réseau ;
- timeout ;
- caster indisponible.

---

## Gestion RTCM

Écrire les bytes bruts vers le port série GNSS.

```python
rtcm_data = sock.recv(4096)
gnss_write_rtcm(rtcm_data)
```

---

## Accès série partagé

Le même port série est utilisé pour :
- lire les trames NMEA ;
- écrire les corrections RTCM.

Règle :
- lecture continue dans `GnssThread`;
- écriture protégée par `serial_lock`;
- ne pas fermer le port depuis le thread NTRIP.

---

## Gestion des erreurs GNSS

Prévoir :
- reconnexion automatique si `/dev/ttyUSB0` disparaît ;
- timeout si aucune trame valide depuis plusieurs secondes ;
- logs des erreurs checksum/parsing ;
- fermeture propre du port série à l’arrêt ;
- redémarrage de la lecture après reconnexion.

---

## Gestion des erreurs NTRIP

Prévoir :
- reconnexion automatique ;
- backoff progressif ;
- renvoi GGA après reconnexion ;
- logs clairs en cas d’authentification incorrecte ;
- logs si mountpoint invalide ;
- reprise automatique après perte réseau.

---

## Gestion des dépendances

Gestion de l’environnement virtuel et des dépendances du projet avec uv run, uv sync et uv lock.

---

