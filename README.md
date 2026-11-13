# Nextcloud Docker Compose

Stack Docker Compose per Nextcloud con database MySQL/MariaDB, Redis e phpMyAdmin. Le immagini, le credenziali, le porte e i nomi dei volumi si configurano tramite `.env`.

## Avvio

Copiare `.env.example` in `.env`, personalizzare i valori e avviare lo stack:

```bash
cp .env.example .env
docker compose -f docker-compose.yaml up -d
```

Per gli aggiornamenti consultare [How-To-Upgrade.md](How-To-Upgrade.md). Prima di aggiornare effettuare un backup dei dati e del database.


## HTTPS / SSL con gateway esterno

È possibile abilitare HTTPS usando [docker-ssl-gataway](https://github.com/Nanobarbalunga/docker-ssl-gataway), un gateway Docker Compose separato con Caddy, Certbot e rinnovo automatico tramite timer systemd utente.

La guida include gli override `docker-compose.ssl.override.yaml` per Nextcloud e OpenWebUI, la configurazione del reverse proxy e il mantenimento delle porte pubbliche esistenti. La validazione HTTP-01 richiede un inoltro permanente della porta pubblica TCP 80 verso il responder ACME del Docker host.

La repository del gateway è privata: il collegamento richiede un account autorizzato.
