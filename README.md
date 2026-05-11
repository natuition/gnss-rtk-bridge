# GNSS-RTK-bridge

Service Python qui lit un flux NMEA GNSS, publie un état en UDP Protobuf, et applique les corrections RTCM venant d un caster NTRIP.

## Prérequis

- Python 3.11+
- uv installé
- protoc installé

Exemple Debian Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y protobuf-compiler
```

## Installation avec uv

Depuis la racine du projet:

```bash
uv sync
```

## Génération Protobuf

Le fichier proto est dans [protos/gnss_fix.proto](protos/gnss_fix.proto).

Générer le module Python dans le meme dossier que le proto:

```bash
protoc \
  --proto_path=protos \
  --python_out=protos \
  protos/gnss_fix.proto
```

Le fichier attendu est [protos/gnss_fix_pb2.py](protos/gnss_fix_pb2.py).

Important: apres toute modification de [protos/gnss_fix.proto](protos/gnss_fix.proto), relancer la commande `protoc`.

## Configuration

Copier puis adapter [config.ini](config.ini):

- section serial: port GNSS, baudrate, timeout
- section udp: destination locale du message protobuf
- section ntrip: caster, mountpoint, identifiants, stratégie de reprise

## Lancement avec uv

Deux options simples:

```bash
uv run gnss-rtk-bridge --config config.ini --log-level INFO
```

Sans `--log-level`, le niveau par defaut est `INFO`.
Pour voir les logs NTRIP RTCM detaillees (`NTRIP RTCM -> ...`), utiliser `--log-level DEBUG`.

## Client UDP lisible dans le terminal

Un client est disponible dans le package: [gnss_rtk_bridge/udp_gnssfix_client.py](gnss_rtk_bridge/udp_gnssfix_client.py).

Il affiche le dernier paquet en rafraichissant l ecran sur place (sans spam de lignes).

```bash
uv run udp-gnssfix-client --host 127.0.0.1 --port 5010
```
