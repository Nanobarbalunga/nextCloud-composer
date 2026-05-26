# How To Upgrade Nextcloud

Questa guida spiega come aggiornare **solo il container Nextcloud** alla versione più recente o a una versione specifica, senza perdere configurazioni, installazione iniziale, utenti, profili e file caricati.

Il principio fondamentale è semplice: **l'immagine Docker può essere aggiornata, ma i volumi persistenti non devono essere eliminati**.

> ⚠️ Non usare mai `docker compose down -v` durante un upgrade, perché l'opzione `-v` elimina i volumi Docker e può causare perdita di dati.

---

## 1. Verificare la versione attuale di Nextcloud

Esegui:

```bash

docker compose exec --user www-data nextcloud php occ status
```
Spiegazione del comando:

docker compose exec: esegue un comando dentro un container già avviato dal progetto Compose.
--user www-data: esegue il comando come utente web server usato da Nextcloud.
nextcloud: è il nome del servizio definito nel docker-compose.yaml.
php occ status: esegue il comando CLI di Nextcloud e mostra informazioni come versione installata, stato manutenzione e stato dell'installazione.

## 2. Controllare il percorso di upgrade supportato

Nextcloud non deve essere aggiornato saltando major version.

Esempio corretto:
```bash

30.x -> 31.x -> 32.x -> 33.x
```

Esempio da evitare:
```bash

30.x -> 33.x
```

Se vuoi aggiornare alla versione più recente, procedi una major alla volta.

## 3. Verificare i volumi usati dal progetto

Esegui:
```bash

docker compose config --volumes
```

Spiegazione del comando:

- docker compose config: mostra la configurazione Compose finale dopo aver letto docker-compose.yaml e .env.
- --volumes: mostra solo i volumi dichiarati nel progetto.

Nel progetto sono previsti tre volumi principali:

- nextcloud_data
- db_data
- redis_data

Nel docker-compose.yaml questi nomi sono volumi logici Compose. Il nome reale del volume Docker può essere configurato tramite .env.

Esempio:
```bash

NEXTCLOUD_DATA_VOLUME=nc_nextcloud_data
DB_DATA_VOLUME=nc_database_data
REDIS_DATA_VOLUME=nc_redis_data
```
## 4. Preparare una cartella per i backup

Esegui:
```bash

mkdir -p backups
```

Spiegazione del comando:

- mkdir: crea una directory.
- -p: evita errori se la directory esiste già.
- backups: è la cartella locale in cui verranno salvati i backup.

## 5. Eseguire il backup del database

Esegui:
```bash

docker compose exec -T db sh -lc 'command -v mariadb-dump >/dev/null && DUMP=mariadb-dump || DUMP=mysqldump; "$DUMP" -u root -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' > backups/nextcloud-db-backup.sql
```
Spiegazione del comando:

- docker compose exec: esegue un comando dentro un servizio Compose già avviato.
- -T: disabilita la pseudo-TTY, utile quando si redirige l'output verso un file.
- db: è il servizio del database definito nel docker-compose.yaml.
- sh -lc '...': apre una shell nel container ed esegue il comando passato come stringa.
- command -v mariadb-dump >/dev/null: verifica se nel container esiste mariadb-dump.
- && DUMP=mariadb-dump || DUMP=mysqldump: usa mariadb-dump se disponibile, altrimenti mysqldump.
- "$DUMP": esegue il comando di dump scelto.
- -u root: usa l'utente root del database.
- -p"$MYSQL_ROOT_PASSWORD": usa la password root definita nel container database.
- "$MYSQL_DATABASE": esporta il database configurato nel container.
- > backups/nextcloud-db-backup.sql: salva il dump SQL sul filesystem host.

## 6. Eseguire il backup del volume Nextcloud

Esegui:
```bash

NEXTCLOUD_VOLUME=$(docker compose config --format json | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data["volumes"]["nextcloud_data"].get("name", "nextcloud_data"))')
```

Spiegazione del comando:

- NEXTCLOUD_VOLUME=...: salva in una variabile shell il nome reale del volume Docker usato da Nextcloud.
- docker compose config --format json: stampa la configurazione Compose finale in formato JSON.
- python3 -c '...': legge il JSON e recupera il nome reale associato al volume logico nextcloud_data.
- .get("name", "nextcloud_data"): usa il nome esplicito del volume se presente, altrimenti usa nextcloud_data.

Poi esegui:
```bash

docker run --rm \
  -v "${NEXTCLOUD_VOLUME}:/source:ro" \
  -v "$PWD/backups:/backup" \
  alpine \
  tar czf /backup/nextcloud-data-backup.tar.gz -C /source .
```

Spiegazione del comando:

- docker run: avvia un container temporaneo.
- --rm: elimina il container temporaneo al termine dell'esecuzione.
- -v "${NEXTCLOUD_VOLUME}:/source:ro": monta il volume Nextcloud in /source, in sola lettura.
- :ro: significa read-only.
- -v "$PWD/backups:/backup": monta la cartella locale backups dentro il container.
- alpine: usa una piccola immagine Linux temporanea.
- tar czf /backup/nextcloud-data-backup.tar.gz: crea un archivio compresso.
- -C /source .: entra in /source e archivia tutto il contenuto del volume.

## 7. Eseguire il backup del volume database

Esegui:
```bash

DB_VOLUME=$(docker compose config --format json | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data["volumes"]["db_data"].get("name", "db_data"))')
```

Spiegazione del comando:

- DB_VOLUME=...: salva in una variabile shell il nome reale del volume Docker usato dal database.
- docker compose config --format json: genera la configurazione Compose finale in JSON.
- python3 -c '...': estrae dal JSON il nome reale del volume associato a db_data.

Poi esegui:
```bash

docker run --rm \
  -v "${DB_VOLUME}:/source:ro" \
  -v "$PWD/backups:/backup" \
  alpine \
  tar czf /backup/database-data-backup.tar.gz -C /source .
```

Spiegazione del comando:

- docker run --rm: avvia un container temporaneo e lo elimina al termine.
- -v "${DB_VOLUME}:/source:ro": monta il volume database in sola lettura.
- -v "$PWD/backups:/backup": monta la cartella locale dei backup.
- alpine: usa un'immagine minimale per eseguire tar.
- tar czf: crea un archivio compresso.
- /backup/database-data-backup.tar.gz: è il file finale del backup.
- -C /source .: archivia tutto il contenuto del volume database.

Nota: il dump SQL del database è generalmente il backup più importante. Il backup del volume database è una sicurezza aggiuntiva.

## 8. Attivare la maintenance mode

Esegui:
```bash

docker compose exec --user www-data nextcloud php occ maintenance:mode --on
```

Spiegazione del comando:

- docker compose exec: esegue un comando nel container Nextcloud.
- --user www-data: usa l'utente corretto per i file Nextcloud.
- nextcloud: è il servizio applicativo.
- php occ maintenance:mode --on: abilita la modalità manutenzione.

La maintenance mode impedisce l'uso normale dell'applicazione durante l'upgrade.

## 9. Modificare solo l'immagine Nextcloud nel file .env

Apri il file .env e modifica solo questa variabile:
```bash

NEXTCLOUD_IMAGE=nextcloud:stable-apache
```

Oppure usa una versione specifica:
```bash

NEXTCLOUD_IMAGE=nextcloud:33.0.3-apache
```
Spiegazione:

- NEXTCLOUD_IMAGE: indica quale immagine Docker usare per il servizio Nextcloud.
- nextcloud:stable-apache: usa il ramo stabile dell'immagine Nextcloud con Apache incluso.
- nextcloud:33.0.3-apache: usa una versione precisa e più controllabile.

Durante un upgrade di solo Nextcloud, non modificare queste variabili:
```bash

DB_IMAGE=
REDIS_IMAGE=
PHPMYADMIN_IMAGE=
NEXTCLOUD_DATA_VOLUME=
DB_DATA_VOLUME=
REDIS_DATA_VOLUME=
```

Cambiare i nomi dei volumi può far partire l'applicazione con volumi nuovi e apparentemente vuoti.

## 10. Scaricare la nuova immagine Nextcloud

Esegui:
```bash

docker compose pull nextcloud
```

Spiegazione del comando:

- docker compose pull: scarica le immagini definite nel Compose.
- nextcloud: limita l'operazione al solo servizio Nextcloud.

Questo comando non ricrea i container e non modifica i volumi.

## 11. Ricreare solo il container Nextcloud

Esegui:
```bash

docker compose up -d --no-deps nextcloud
```

Spiegazione del comando:

- docker compose up: crea o aggiorna i container definiti nel Compose.
- -d: avvia i container in background.
- --no-deps: non ricrea e non riavvia le dipendenze del servizio.
- nextcloud: limita l'operazione al solo servizio Nextcloud.

Questo è il passaggio chiave per aggiornare solo Nextcloud senza aggiornare database, Redis o phpMyAdmin.

## 12. Controllare i log di Nextcloud

Esegui:
```bash

docker compose logs -f nextcloud
```

Spiegazione del comando:

- docker compose logs: mostra i log dei container del progetto.
- -f: segue i log in tempo reale.
- nextcloud: limita la visualizzazione ai log del servizio Nextcloud.

Per uscire dalla visualizzazione dei log premi CTRL + C.

## 13. Eseguire l'upgrade applicativo con OCC se richiesto

Controlla lo stato:
```bash

docker compose exec --user www-data nextcloud php occ status
```

Spiegazione:

Mostra lo stato dell'installazione Nextcloud.
Permette di capire se la versione è stata aggiornata correttamente.
Permette di vedere se Nextcloud richiede ancora un upgrade interno.

Se Nextcloud richiede l'upgrade, esegui:
```bash

docker compose exec --user www-data nextcloud php occ upgrade
```

Spiegazione del comando:

- docker compose exec: esegue il comando nel container Nextcloud.
- --user www-data: usa l'utente corretto per Nextcloud.
- php occ upgrade: avvia le migrazioni applicative di Nextcloud.
- 
## 14. Eseguire i comandi di manutenzione database consigliati

Esegui:
```bash

docker compose exec --user www-data nextcloud php occ db:add-missing-indices
```

Spiegazione:

- php occ db:add-missing-indices: aggiunge eventuali indici mancanti nel database.

Esegui:
```bash

docker compose exec --user www-data nextcloud php occ db:add-missing-columns
```

Spiegazione:

- php occ db:add-missing-columns: aggiunge eventuali colonne mancanti nelle tabelle del database.

Esegui:
```bash

docker compose exec --user www-data nextcloud php occ db:add-missing-primary-keys
```
Spiegazione:

- php occ db:add-missing-primary-keys: aggiunge eventuali chiavi primarie mancanti.

Esegui:
```bash

docker compose exec --user www-data nextcloud php occ maintenance:repair
```

Spiegazione:

- php occ maintenance:repair: esegue riparazioni e controlli interni di Nextcloud.
- 
## 15. Disattivare la maintenance mode

Esegui:
```bash

docker compose exec --user www-data nextcloud php occ maintenance:mode --off
```

Spiegazione del comando:

- docker compose exec: esegue un comando nel container Nextcloud.
- --user www-data: usa l'utente corretto per Nextcloud.
- php occ maintenance:mode --off: disattiva la modalità manutenzione.
- 
## 16. Verificare lo stato finale

Esegui:
```bash

docker compose exec --user www-data nextcloud php occ status
```
Spiegazione:

Verifica la versione finale di Nextcloud.
Verifica se l'installazione è attiva.
Verifica se la maintenance mode è disabilitata.

Poi controlla i container:
```bash

docker compose ps
```

Spiegazione:

- docker compose ps: mostra lo stato dei container del progetto.

## 17. Controllare l'interfaccia web

Apri Nextcloud dal browser usando la porta configurata nel .env.

Esempio:

- http://localhost:8080

Verifica:

- login amministratore;
- utenti già creati;
- file caricati;
- app installate;
- pagina di amministrazione;
- eventuali avvisi di sicurezza o manutenzione.
  
## 18. Procedura rapida riassuntiva
```bash

docker compose exec --user www-data nextcloud php occ status
mkdir -p backups

docker compose exec -T db sh -lc 'command -v mariadb-dump >/dev/null && DUMP=mariadb-dump || DUMP=mysqldump; "$DUMP" -u root -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' > backups/nextcloud-db-backup.sql

docker compose exec --user www-data nextcloud php occ maintenance:mode --on

docker compose pull nextcloud
docker compose up -d --no-deps nextcloud

docker compose logs -f nextcloud

docker compose exec --user www-data nextcloud php occ upgrade
docker compose exec --user www-data nextcloud php occ db:add-missing-indices
docker compose exec --user www-data nextcloud php occ db:add-missing-columns
docker compose exec --user www-data nextcloud php occ db:add-missing-primary-keys
docker compose exec --user www-data nextcloud php occ maintenance:repair

docker compose exec --user www-data nextcloud php occ maintenance:mode --off
docker compose exec --user www-data nextcloud php occ status
docker compose ps

Prima di eseguire docker compose pull nextcloud, ricordati di modificare NEXTCLOUD_IMAGE nel file .env.
```
## 19. Cose da non fare

Non eseguire:
```bash
docker compose down -v
```
Perché:

- down ferma e rimuove i container del progetto;
- -v rimuove anche i volumi dichiarati;
- rimuovere i volumi significa perdere database, dati applicativi e file persistenti se non hai backup validi.

Non cambiare i nomi dei volumi nel .env durante l'upgrade:
```bash

NEXTCLOUD_DATA_VOLUME=
DB_DATA_VOLUME=
REDIS_DATA_VOLUME=
```

Non aggiornare contemporaneamente database, Redis e Nextcloud se l'obiettivo è aggiornare solo Nextcloud.

## 20. Rollback di emergenza

Se l'upgrade fallisce, la strategia più sicura è tornare all'immagine precedente e ripristinare il backup del database se le migrazioni erano già partite.

Esempio:
```bash

NEXTCLOUD_IMAGE=nextcloud:VERSIONE_PRECEDENTE-apache
```
Poi esegui:
```bash

docker compose pull nextcloud
docker compose up -d --no-deps nextcloud
```
Spiegazione:

- docker compose pull nextcloud: scarica l'immagine precedente indicata nel .env.
- docker compose up -d --no-deps nextcloud: ricrea solo il container Nextcloud usando quell'immagine.

Se il database è già stato migrato, potrebbe essere necessario ripristinare il dump SQL creato prima dell'upgrade.


---


La procedura spiega come aggiornare **solo il servizio Nextcloud**, mantenendo invariati i volumi Docker persistenti usati per dati applicativi, database e Redis.

Prima di eseguire un upgrade è importante:

- verificare la versione attuale con `occ status`;
- non saltare major version di Nextcloud;
- creare un backup del database e dei volumi;
- non eseguire `docker compose down -v`;
- non cambiare i nomi dei volumi nel file `.env` durante l'upgrade;
- aggiornare solo la variabile `NEXTCLOUD_IMAGE` se l'obiettivo è aggiornare solo Nextcloud.

Esempio sintetico:

```bash
docker compose exec --user www-data nextcloud php occ status
docker compose exec --user www-data nextcloud php occ maintenance:mode --on
docker compose pull nextcloud
docker compose up -d --no-deps nextcloud
docker compose exec --user www-data nextcloud php occ upgrade
docker compose exec --user www-data nextcloud php occ maintenance:mode --off
```
