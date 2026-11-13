# Nextcloud Docker Compose

Stack Docker Compose per Nextcloud con database MySQL/MariaDB, Redis e phpMyAdmin. Le immagini, le credenziali, le porte e i nomi dei volumi si configurano tramite `.env`.

## Avvio

Copiare `.env.example` in `.env`, personalizzare i valori e avviare lo stack:

```bash
cp .env.example .env
docker compose -f docker-compose.yaml up -d
```

Per gli aggiornamenti consultare [How-To-Upgrade.md](How-To-Upgrade.md). Prima di aggiornare effettuare un backup dei dati e del database.


## Backup con controllo spazio e compressione

Lo script [script/nextcloud-backup.py](script/nextcloud-backup.py) salva database MySQL/MariaDB, l'intera directory `/var/www/html` (configurazione, applicazioni e file locali), i Compose e il `.env`. Prima stima le dimensioni, verifica lo spazio e prealloca i file del backup; poi attiva la manutenzione e ferma il frontend. Il database resta acceso. Al termine, o in caso di errore gestibile, tenta di riavviare Nextcloud e ripristina lo stato di manutenzione iniziale.

### Requisiti e limiti

- Python 3.9 o successivo su Linux, Docker Compose e accesso al daemon Docker con l'utente corrente.
- Servizi chiamati `nextcloud` e `db`, database configurato con `MYSQL_DATABASE`, `MYSQL_USER` e `MYSQL_PASSWORD` nel container `db`.
- Cartella di destinazione **già esistente**, scrivibile e fuori dai mount Nextcloud. Verificare che l'eventuale disco esterno sia effettivamente montato.
- Filesystem con supporto a `posix_fallocate`. La preallocazione di SQL/TAR avviene prima della manutenzione; se fallisce, Nextcloud non viene fermato.
- Directory dati locale dentro `/var/www/html`; directory dati esterne vengono rifiutate. Archivi esterni SMB/S3 non sono inclusi.
- Non eseguire aggiornamenti o processi esterni che scrivano sugli stessi dati durante il backup.
- Il backup contiene credenziali e dati personali: file e directory hanno permessi riservati, ma **non sono cifrati**.

### Solo stima

Dalla cartella principale della repository, con l'override HTTPS presente:

```bash
mkdir -p "/mnt/backup/nextcloud"
python3 script/nextcloud-backup.py "/mnt/backup/nextcloud" --estimate
```

Modificare il percorso a piacere. La stima mostra dimensione dei file, dimensione fisica indicativa del database, spazio da allocare e riserva. Il dump SQL può avere dimensioni diverse dal database: lo script usa un margine prudente. Le percentuali del backup sono riferite alla stima, non a una dimensione finale già nota.

### Eseguire il backup

```bash
python3 script/nextcloud-backup.py "/mnt/backup/nextcloud"
```

Per lasciare almeno 5 GiB liberi:

```bash
python3 script/nextcloud-backup.py "/mnt/backup/nextcloud" --min-free-gib 5
```

Il margine dell'archivio TAR è configurabile con `--margin-percent` (default 20). Se il backup supera la stima, lo script controlla lo spazio prima di estendere l'allocazione. Quote, copy-on-write e scritture concorrenti possono comunque causare errori: il controllo non è una garanzia assoluta sul filesystem.

### ZIP e 7z con preset

La compressione parte **dopo il riavvio del frontend Nextcloud**. La cartella originale viene sempre conservata, anche dopo una compressione riuscita; occorre quindi spazio per entrambe. Il controllo iniziale comprende una stima prudente dell'archivio aggiuntivo, senza presumere che i dati siano comprimibili.

| Opzione | Funzione |
| --- | --- |
| `--archive none` | Solo cartella di backup, default |
| `--archive zip` | ZIP Deflate tramite Python, senza dipendenze aggiuntive |
| `--archive 7z` | 7z LZMA2 tramite `7zz` oppure `7z`, da installare sull'host |
| `--preset fast` | Livello 1: privilegia la velocità |
| `--preset balanced` | Livello 5: compromesso, default |
| `--preset max` | Livello 9: compressione più intensa, maggiore costo CPU/tempo e potenzialmente RAM |

ZIP veloce:

```bash
python3 script/nextcloud-backup.py "/mnt/backup/nextcloud" --archive zip --preset fast
```

7z con compressione massima:

```bash
python3 script/nextcloud-backup.py "/mnt/backup/nextcloud" --archive 7z --preset max
```

Stima comprensiva di ZIP:

```bash
python3 script/nextcloud-backup.py "/mnt/backup/nextcloud" --archive zip --estimate
```

ZIP viene preallocato e verificato tramite CRC. Per 7z lo spazio è controllato prima e durante la compressione, mentre l'allocazione del file è gestita dal programma esterno; al termine viene eseguito il test integrità di 7-Zip. Entrambi ricevono un checksum SHA256 esterno. Il TAR dentro l'archivio conserva i metadati Unix dei file Nextcloud. I preset non garantiscono dimensioni più piccole su dati già compressi.

### Senza override HTTPS o da un'altra directory

Per usare soltanto il Compose originale:

```bash
python3 script/nextcloud-backup.py "/mnt/backup/nextcloud" -f docker-compose.yaml
```

Per indicare la cartella del progetto:

```bash
python3 /percorso/nextCloud-composer/script/nextcloud-backup.py "/mnt/backup/nextcloud" --project-dir /percorso/nextCloud-composer
```

L'opzione `-f` è ripetibile e sostituisce la lista predefinita dei Compose. Sono disponibili anche `--service` e `--db-service` per nomi di servizio diversi. Tutte le opzioni:

```bash
python3 script/nextcloud-backup.py --help
```


### Parametri brevi, timestamp e guida

Ogni opzione lunga ha un alias breve. Se più opzioni iniziano con la stessa lettera, si usa una lettera diversa o maiuscola: `-p` indica sempre `--preset`, mentre `-P` indica `--project-dir`. Gli alias sono sensibili a maiuscole/minuscole.

| Alias | Opzione lunga | Funzione |
| --- | --- | --- |
| `-h` | `--help` | Mostra la guida, i parametri e gli esempi |
| `-t` | `--timestamp` | Aggiunge `_DATA_ORA_DI_AVVIO` al nome della cartella e, se richiesto, dell’archivio |
| `-a` | `--archive` | `none`, `zip` oppure `7z` |
| `-p` | `--preset` | `fast`, `balanced` oppure `max` |
| `-P` | `--project-dir` | Cartella del progetto Compose |
| `-f` | `--file` | File Compose, ripetibile e in ordine |
| `-s` | `--service` | Servizio Nextcloud |
| `-d` | `--db-service` | Servizio database |
| `-m` | `--margin-percent` | Margine percentuale della stima TAR |
| `-M` | `--min-free-gib` | Riserva di spazio libero in GiB |
| `-e` | `--estimate` | Solo stima, senza preallocare o fermare il frontend |

Esempi equivalenti, con timestamp di avvio e ZIP bilanciato:

```bash
python3 script/nextcloud-backup.py "/mnt/backup/nextcloud" --timestamp --archive zip --preset balanced
python3 script/nextcloud-backup.py "/mnt/backup/nextcloud" -t -a zip -p balanced
```

Per backup ripetuti nella stessa destinazione usare `-t`. Il nome risultante è, per esempio, `nextcloud_20261113_143000_123456` e l’archivio `nextcloud_20261113_143000_123456.zip`. Il timestamp è nell’ora locale del Docker host.

La guida funziona con entrambe le forme:

```bash
python3 script/nextcloud-backup.py -h
python3 script/nextcloud-backup.py --help
```

Lanciando lo script senza argomenti viene mostrato un messaggio con il rimando a `-h` / `--help`; termina con codice 2 senza interrogare Docker o avviare il backup.

### Contenuto e verifica

Senza `--timestamp` il backup usa la sottocartella `nextcloud`; con `--timestamp` / `-t` usa `nextcloud_DATA_ORA_DI_AVVIO` (formato `YYYYMMDD_HHMMSS_microsecondi`). L’istante viene acquisito appena lo script parte, prima della stima. In entrambi i casi un backup preesistente non viene sovrascritto: scegliere `-t` o un’altra destinazione. La sottocartella contiene:

- `database.sql`: dump SQL;
- `nextcloud-html.tar`: archivio non compresso di Nextcloud;
- `configuration/`: copie numerate dei Compose e del `.env`;
- `manifest.json`: versione Nextcloud, immagine, dimensioni e hash;
- `SHA256SUMS`: checksum dei file;
- `SUCCESS`: indicatore di completamento.

Una cartella o un archivio con suffisso `.partial` è incompleto. Se fallisce solo la compressione, la cartella originale già completata rimane valida. Non vengono eliminati backup precedenti.

Per ricontrollare i file, entrare nella cartella del backup:

```bash
sha256sum -c SHA256SUMS
```

Per un archivio compresso verificare anche il relativo file `.zip.sha256` o `.7z.sha256` dalla directory che lo contiene. Il controllo di integrità non sostituisce una prova di ripristino su un'istanza separata. Nel ripristino la configurazione salvata potrebbe contenere la manutenzione attiva, che va disattivata esplicitamente a fine procedura.

Ctrl+C e SIGTERM attivano il recupero del servizio. SIGKILL o perdita di alimentazione non sono gestibili: verificare manualmente il container e la modalità manutenzione prima di riprendere l'uso.


## HTTPS / SSL con gateway esterno

È possibile abilitare HTTPS usando [docker-ssl-gateway](https://github.com/Nanobarbalunga/docker-ssl-gateway), un gateway Docker Compose separato con Caddy, Certbot e rinnovo automatico tramite timer systemd utente.

La guida include gli override `docker-compose.ssl.override.yaml` per Nextcloud e OpenWebUI, la configurazione del reverse proxy e il mantenimento delle porte pubbliche esistenti. La validazione HTTP-01 richiede un inoltro permanente della porta pubblica TCP 80 verso il responder ACME del Docker host.

La repository del gateway è privata: il collegamento richiede un account autorizzato.
