#!/usr/bin/env python3
"""Backup Nextcloud Docker Compose con stima, spazio riservato e avanzamento.

Uso (dalla cartella Compose Nextcloud):
  python3 nextcloud-backup.py /mnt/backups/nextcloud --estimate
  python3 nextcloud-backup.py /mnt/backups/nextcloud
  python3 nextcloud-backup.py /mnt/backups/nextcloud --archive zip --preset fast
  python3 nextcloud-backup.py /mnt/backups/nextcloud --archive 7z --preset max

Python >= 3.9, Linux, Docker Compose e accesso al daemon senza sudo.
Default: docker-compose.yaml + docker-compose.ssl.override.yaml;
servizi nextcloud e db; database MySQL/MariaDB con MYSQL_* nel servizio db.
I file utenti devono essere sotto /var/www/html, senza storage esterni.
Non aggiorna le immagini e non elimina backup precedenti.

--archive none|zip|7z (default none); --preset fast|balanced|max.
Preset livelli 1/5/9: più compressione richiede più CPU/tempo (e RAM con 7z).
ZIP usa Python, senza pacchetti aggiuntivi; 7z richiede 7zz o 7z.
La compressione avviene DOPO il riavvio di Nextcloud e conserva la cartella
originale. Viene stimato lo spazio aggiuntivo senza supporre una riduzione.
ZIP è preallocato; per 7z lo spazio viene controllato prima e durante, ma il
file è allocato dal programma esterno. Su file incomprimibili può crescere.
L'archivio viene verificato (CRC/test 7z) e accompagnato da un hash SHA256.
Un archivio .partial non è valido; la cartella originale rimane disponibile.
Gli archivi non sono cifrati. Il TAR interno conserva i metadati Unix.

Output: database.sql, nextcloud-html.tar (non compresso), configuration/,
manifest.json, SHA256SUMS e SUCCESS. Una cartella .partial segnala un errore.
Il dump SQL usa una stima prudente; le percentuali sono riferite alla stima.
La destinazione deve essere esterna ai mount Nextcloud e supportare fallocate.
Il filesystem può applicare quote, compressione o copy-on-write: spazio stimato
ed effettivo non sono una garanzia assoluta; errori di scrittura sono gestiti.
Durante il backup Nextcloud è fermo. Non avviare aggiornamenti/cron esterni
che scrivano nello stesso database o volume. Storage SMB/S3 non è incluso.
SIGINT/SIGTERM attivano il recupero; SIGKILL e perdita di alimentazione no.
Se il backup viene interrotto brutalmente, riavviare il servizio e disattivare
la manutenzione manualmente con gli stessi file Compose.
Il backup include credenziali e dati personali: nessuna cifratura automatica.
Dopo il backup verificare SHA256SUMS e provare un ripristino separato.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

MIB = 1024 ** 2
GIB = 1024 ** 3


def human(n):
    for u in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if n < 1024 or u == 'TiB':
            return f'{n:.1f} {u}'
        n /= 1024


def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode:
        # Non stampare argomenti/env: potrebbero contenere dati sensibili.
        raise RuntimeError(f'Comando {cmd[0]} fallito ({p.returncode}): {p.stderr.strip()[-3000:]}')
    return p.stdout.strip()


def space(path, needed, reserve):
    free = shutil.disk_usage(path).free
    if free < needed + reserve:
        raise RuntimeError(f'Spazio insufficiente: liberi {human(free)}, da allocare {human(needed)}, riserva {human(reserve)}.')
    return free


class Output:
    """Preallocazione fisica; niente file sparse creati con truncate."""
    def __init__(self, path, capacity, reserve):
        self.path, self.capacity, self.reserve = path, capacity, reserve
        self.written, self.digest = 0, hashlib.sha256()
        self.file = open(path, 'xb', buffering=0)
        try:
            os.posix_fallocate(self.file.fileno(), 0, capacity)
        except BaseException:
            self.file.close()
            path.unlink(missing_ok=True)
            raise

    def write(self, data):
        required = self.written + len(data)
        if required > self.capacity:
            growth = max(required - self.capacity, 64 * MIB)
            space(self.path.parent, growth, self.reserve)
            print(f'\nStima superata per {self.path.name}: alloco altri {human(growth)}.', flush=True)
            os.posix_fallocate(self.file.fileno(), self.capacity, growth)
            self.capacity += growth
        view = memoryview(data)
        while view:
            n = self.file.write(view)
            if not n:
                raise OSError('Scrittura incompleta')
            view = view[n:]
        self.written += len(data)
        self.digest.update(data)

    def close(self):
        if not self.file.closed:
            try:
                os.ftruncate(self.file.fileno(), self.written)
                os.fsync(self.file.fileno())
            finally:
                self.file.close()


def stream(cmd, output, label):
    print(f'{label}...', flush=True)
    start = last = time.monotonic()
    # stderr su file per non bloccare il processo durante un flusso molto grande.
    with tempfile.TemporaryFile() as errors:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errors, start_new_session=True)
        try:
            while True:
                chunk = p.stdout.read(MIB)
                if not chunk:
                    break
                output.write(chunk)
                now = time.monotonic()
                if now - last >= 0.5:
                    elapsed = max(now - start, 0.001)
                    pct = min(99.9, output.written * 100 / output.capacity)
                    print(f'\r{label}: {pct:5.1f}% della stima | {human(output.written)} | {human(output.written / elapsed)}/s', end='', flush=True)
                    last = now
            rc = p.wait()
            if rc:
                errors.seek(0)
                raise RuntimeError(f'{label} fallito ({rc}): {errors.read().decode(errors="replace")[-3000:]}')
            if not output.written:
                raise RuntimeError(f'{label}: nessun dato ricevuto')
            print(f'\r{label}: completato | {human(output.written)} | {time.monotonic() - start:.1f}s' + ' ' * 15, flush=True)
        finally:
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
            p.stdout.close()
            output.close()



def interrupt_signal(*_):
    raise KeyboardInterrupt()


def archive_backup(folder, kind, preset, reserve):
    """Comprimi solo un backup completato, dopo il recupero di Nextcloud.

    ZIP viene scritto in un file fisicamente preallocato. Per 7z il file viene
    gestito dal programma esterno: controlliamo lo spazio, senza dichiarare
    una preallocazione del file 7z che l'archiver non garantisce di conservare.
    Il backup originale non viene mai rimosso.
    """
    items = sorted(p for p in folder.rglob('*') if p.is_file())
    total = sum(p.stat().st_size for p in items)
    capacity = math.ceil(total * 1.10) + len(items) * 4096 + 16 * MIB
    space(folder.parent, capacity, reserve)
    target = folder.with_name(folder.name + '.' + kind)
    partial = folder.with_name(folder.name + '.' + kind + '.partial')
    if target.exists() or partial.exists():
        raise RuntimeError('Archivio esistente: non viene sovrascritto')
    levels = {'fast': 1, 'balanced': 5, 'max': 9}
    level = levels[preset]
    print(f'Compressione {kind}, preset {preset} (livello {level}); spazio prudenziale {human(capacity)}.', flush=True)
    if kind == 'zip':
        print('Prealloco il file ZIP...', flush=True)
        with open(partial, 'x+b') as handle:
            os.posix_fallocate(handle.fileno(), 0, capacity)
            written = done = 0
            start = last = time.monotonic()
            try:
                with zipfile.ZipFile(handle, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=level, allowZip64=True) as archive:
                    for item in items:
                        name = str(Path(folder.name) / item.relative_to(folder))
                        with item.open('rb') as source, archive.open(name, 'w', force_zip64=True) as dest:
                            while True:
                                chunk = source.read(MIB)
                                if not chunk:
                                    break
                                # Margine extra ZIP; fermarsi prima di consumare la riserva.
                                if handle.tell() + 4 * MIB > capacity:
                                    growth = 64 * MIB
                                    space(folder.parent, growth, reserve)
                                    os.posix_fallocate(handle.fileno(), capacity, growth)
                                    capacity += growth
                                dest.write(chunk)
                                done += len(chunk)
                                now = time.monotonic()
                                if now - last >= .5:
                                    print(f'\rZIP: {done / max(1,total) * 100:5.1f}% | {human(done)} letti | {human(done / max(.001,now-start))}/s', end='', flush=True)
                                    last = now
                written = handle.tell()
            finally:
                # In caso di errore resta un archivio parziale esplicitamente nominato.
                if not written:
                    written = handle.tell()
                handle.truncate(written)
                handle.flush()
                os.fsync(handle.fileno())
        print('\nVerifico integrità ZIP (CRC)...', flush=True)
        with zipfile.ZipFile(partial, 'r') as archive:
            bad = archive.testzip()
            if bad:
                raise RuntimeError(f'CRC ZIP non valido: {bad}')
    else:
        binary = shutil.which('7zz') or shutil.which('7z')
        if not binary:
            raise RuntimeError('7z richiede il programma 7zz oppure 7z installato sul Docker host')
        # LZMA2 esplicito; -mx e -bsp1 sono gestiti dal programma 7-Zip.
        command = [binary, 'a', '-t7z', '-m0=lzma2', f'-mx={level}', '-bsp1', '-y', str(partial), folder.name]
        proc = subprocess.Popen(command, cwd=folder.parent, start_new_session=True)
        try:
            while proc.poll() is None:
                if shutil.disk_usage(folder.parent).free < reserve:
                    raise RuntimeError('Compressione interrotta: raggiunta la riserva di spazio libero')
                time.sleep(.2)
            if proc.returncode:
                raise RuntimeError(f'7-Zip è terminato con codice {proc.returncode}; backup originale conservato')
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        print('Verifico integrità 7z...', flush=True)
        run([binary, 't', '-t7z', str(partial)])
    # Hash esterno dell'archivio, oltre ai checksum dei file contenuti.
    digest = hashlib.sha256()
    with partial.open('rb') as handle:
        while True:
            chunk = handle.read(MIB)
            if not chunk:
                break
            digest.update(chunk)
    partial.rename(target)
    target.with_name(target.name + '.sha256').write_text(f'{digest.hexdigest()}  {target.name}\n')
    print(f'Archivio verificato: {target} ({human(target.stat().st_size)})\nBackup originale conservato: {folder}', flush=True)
    return target


DB_SIZE = '''set -eu
CLIENT=$(command -v mariadb || command -v mysql)
export MYSQL_PWD="$MYSQL_PASSWORD"
exec "$CLIENT" -N -B -u "$MYSQL_USER" "$MYSQL_DATABASE" -e 'SELECT COALESCE(SUM(data_length + index_length),0) FROM information_schema.tables WHERE table_schema=DATABASE()'
'''
DB_DUMP = '''set -eu
DUMP=$(command -v mariadb-dump || command -v mysqldump)
export MYSQL_PWD="$MYSQL_PASSWORD"
exec "$DUMP" --single-transaction --quick --skip-lock-tables --default-character-set=utf8mb4 --hex-blob -u "$MYSQL_USER" "$MYSQL_DATABASE"
'''


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('destination', type=Path, help='Cartella esistente su disco di backup (deve essere scrivibile)')
    ap.add_argument('--project-dir', type=Path, default=Path.cwd(), help='Cartella Compose; default: directory corrente')
    ap.add_argument('-f', '--file', action='append', dest='files', help='File Compose in ordine; ripetibile, sostituisce i default')
    ap.add_argument('--service', default='nextcloud')
    ap.add_argument('--db-service', default='db')
    ap.add_argument('--margin-percent', type=float, default=20, help='Margine stima archivio, default 20%%')
    ap.add_argument('--min-free-gib', type=float, default=1, help='Spazio da lasciare libero, default 1 GiB')
    ap.add_argument('--archive', choices=['none', 'zip', '7z'], default='none', help='Archivio aggiuntivo: none (default), zip o 7z')
    ap.add_argument('--preset', choices=['fast', 'balanced', 'max'], default='balanced', help='Compressione: fast=1, balanced=5, max=9')
    ap.add_argument('--estimate', action='store_true', help='Solo stima: non alloca e non ferma Nextcloud')
    args = ap.parse_args()
    if not math.isfinite(args.margin_percent) or args.margin_percent < 0 or not math.isfinite(args.min_free_gib) or args.min_free_gib < 0:
        ap.error('Margini e riserva devono essere numeri finiti non negativi')
    if not hasattr(os, 'posix_fallocate') or not shutil.which('docker'):
        ap.error('Servono Linux con posix_fallocate e il comando docker')
    if args.archive == '7z' and not (shutil.which('7zz') or shutil.which('7z')):
        ap.error('Installa 7zz o 7z per usare --archive 7z')
    os.umask(0o077)
    project = args.project_dir.expanduser().resolve(strict=True)
    dest = args.destination.expanduser().resolve(strict=True)
    if not dest.is_dir():
        ap.error('La destinazione deve essere una directory esistente; crea/monta prima il disco')
    files = [(project / f).resolve(strict=True) for f in (args.files or ['docker-compose.yaml', 'docker-compose.ssl.override.yaml'])]
    compose = ['docker', 'compose', '--project-directory', str(project)]
    for f in files:
        compose += ['-f', str(f)]
    occ = compose + ['exec', '-T', '--user', 'www-data', args.service, 'php', 'occ']
    # Lock condiviso da tutte le esecuzioni nella stessa cartella di progetto.
    with open(project / '.nextcloud-backup.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Un backup dello stesso progetto è già in esecuzione')
        cid = run(compose + ['ps', '-q', args.service])
        if not cid or '\n' in cid:
            raise RuntimeError('È richiesta una sola istanza Nextcloud avviata')
        info = json.loads(run(['docker', 'inspect', cid]))[0]
        if not info['State']['Running']:
            raise RuntimeError('Nextcloud deve essere avviato per le verifiche iniziali')
        dbid = run(compose + ['ps', '-q', args.db_service])
        if not dbid or '\n' in dbid:
            raise RuntimeError('È richiesta una sola istanza database avviata')
        for mount in info.get('Mounts', []):
            src = Path(mount['Source']).resolve()
            if dest == src or src in dest.parents:
                raise RuntimeError('La destinazione è dentro un mount Nextcloud: scegliere una cartella esterna')
        status = json.loads(run(occ + ['status', '--output=json']))
        if not status.get('installed') or status.get('needsDbUpgrade'):
            raise RuntimeError('Installazione incompleta o aggiornamento DB pendente: completarlo prima del backup')
        datadir = run(occ + ['config:system:get', 'datadirectory'])
        realdata = run(compose + ['exec', '-T', args.service, 'readlink', '-f', datadir])
        if not realdata.startswith('/var/www/html/'):
            raise RuntimeError(f'Directory dati esterna ({realdata}): questo script salva soltanto /var/www/html')
        print('Calcolo dimensioni file (può richiedere tempo)...', flush=True)
        size = int(run(compose + ['exec', '-T', '--user', 'root', args.service, 'du', '-sb', '/var/www/html']).split()[0])
        # Overhead tar per header, padding e nomi lunghi; margine aggiuntivo sul totale.
        count = int(run(compose + ['exec', '-T', '--user', 'root', args.service, 'sh', '-c', "set -eu; find /var/www/html -printf x | wc -c"]))
        dbsize = int(run(compose + ['exec', '-T', args.db_service, 'sh', '-c', DB_SIZE]))
        tar_est = math.ceil((size + count * 2048 + 2 * MIB) * (1 + args.margin_percent / 100))
        # Il dump SQL non ha dimensione uguale ai file fisici del DB.
        sql_est = max(64 * MIB, dbsize * 3 + 16 * MIB)
        config_sources = list(dict.fromkeys(files + ([project / '.env'] if (project / '.env').is_file() else [])))
        configsize = sum(f.stat().st_size for f in config_sources)
        reserve = math.ceil(args.min_free_gib * GIB)
        needed = tar_est + sql_est + configsize + MIB
        archive_est = math.ceil(needed * 1.10) + 16 * MIB if args.archive != 'none' else 0
        free = shutil.disk_usage(dest).free
        print(f'File Nextcloud: {human(size)} ({count} elementi)\nDatabase fisico stimato: {human(dbsize)}\nPreallocazione archivio: {human(tar_est)}\nPreallocazione SQL: {human(sql_est)}\nSpazio libero: {human(free)}\nRichiesto con riserva: {human(needed + archive_est + reserve)}', flush=True)
        print('La dimensione finale è stimata; l’allocazione viene estesa se necessario.', flush=True)
        if archive_est:
            print(f'Spazio aggiuntivo stimato per {args.archive}: {human(archive_est)}; il backup originale viene conservato.', flush=True)
        space(dest, needed + archive_est, reserve)
        if args.estimate:
            print('Stima completata. Nessun file di backup allocato e nessuna manutenzione attivata.')
            return
        stamp = dt.datetime.now().strftime('%Y-%m-%d_%H-%M-%S_%f')
        folder = dest / ('nextcloud-' + stamp + '.partial')
        folder.mkdir(mode=0o700)
        outputs = []
        stop_attempted = maintenance_attempted = False
        original_maintenance = bool(status.get('maintenance'))
        manifest = {'format': 1, 'created_at': dt.datetime.now().astimezone().isoformat(), 'status': 'incomplete', 'datadirectory': realdata, 'nextcloud_version': status.get('versionstring'), 'image': info['Config']['Image'], 'image_id': info['Image'], 'estimates': {'html': tar_est, 'sql': sql_est}, 'files': {}}
        try:
            for name, cap in [('nextcloud-html.tar', tar_est), ('database.sql', sql_est)]:
                print(f'Preallocazione {name}: {human(cap)}...', flush=True)
                space(folder, cap, reserve)
                outputs.append(Output(folder / name, cap, reserve))
            space(folder, configsize + MIB, reserve)
            configs = folder / 'configuration'
            configs.mkdir()
            for i, source in enumerate(config_sources):
                # Nessuna collisione se due file hanno lo stesso basename.
                target = configs / f'{i:02d}-{source.name}'
                shutil.copyfile(source, target)
                manifest['files'][str(target.relative_to(folder))] = {'source': str(source), 'bytes': target.stat().st_size, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
            if not original_maintenance:
                print('Attivo manutenzione Nextcloud...', flush=True)
                maintenance_attempted = True
                run(occ + ['maintenance:mode', '--on'])
            stop_attempted = True
            print('Fermo il frontend Nextcloud; il database resta attivo...', flush=True)
            run(compose + ['stop', args.service])
            stream(compose + ['exec', '-T', args.db_service, 'sh', '-c', DB_DUMP], outputs[1], 'Database')
            stream(['docker', 'cp', cid + ':/var/www/html/.', '-'], outputs[0], 'File Nextcloud')
            print('Verifico la struttura dell’archivio...', flush=True)
            with tarfile.open(folder / 'nextcloud-html.tar', 'r:') as archive:
                has_config = False
                for member in archive:
                    if member.name.endswith('config/config.php'):
                        has_config = True
                if not has_config:
                    raise RuntimeError('config/config.php non trovato: archivio non valido per questa installazione')
            for output in outputs:
                manifest['files'][output.path.name] = {'bytes': output.written, 'sha256': output.digest.hexdigest()}
        finally:
            # Evita che un secondo Ctrl+C interrompa il recupero; SIGKILL/power loss restano non gestibili.
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            recovery_errors = []
            for output in outputs:
                try:
                    output.close()
                except OSError as e:
                    recovery_errors.append(f'Chiusura file: {e}')
            if stop_attempted:
                try:
                    print('Riavvio Nextcloud...', flush=True)
                    run(compose + ['start', args.service])
                except Exception as e:
                    recovery_errors.append(str(e))
            if maintenance_attempted:
                # Il server può impiegare alcuni secondi a tornare eseguibile.
                for attempt in range(12):
                    try:
                        run(occ + ['maintenance:mode', '--off'])
                        break
                    except Exception as e:
                        if attempt == 11:
                            recovery_errors.append(str(e))
                        else:
                            time.sleep(2)
            if original_maintenance:
                print('Manutenzione preesistente mantenuta attiva.', flush=True)
            if recovery_errors:
                raise RuntimeError('Verificare manualmente il servizio: ' + '; '.join(recovery_errors))
        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGTERM, interrupt_signal)
        manifest['status'] = 'complete'
        (folder / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
        checks = ''.join(f'{entry["sha256"]}  {name}\n' for name, entry in manifest['files'].items())
        (folder / 'SHA256SUMS').write_text(checks)
        (folder / 'SUCCESS').write_text('Backup completato; una prova di ripristino resta necessaria.\n')
        final = folder.with_name(folder.name.removesuffix('.partial')) if sys.version_info >= (3, 9) else folder.with_name(folder.name[:-8])
        folder.rename(final)
        print(f'\nBackup completato: {final}\nDati scritti: {human(sum(x["bytes"] for x in manifest["files"].values()))}\nVerifica successiva: cd nella cartella e lancia sha256sum -c SHA256SUMS', flush=True)

        if args.archive != 'none':
            try:
                archive_backup(final, args.archive, args.preset, reserve)
            except BaseException:
                print(f'Compressione non completata. Backup originale valido conservato in: {final}', file=sys.stderr)
                raise


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, interrupt_signal)
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f'\nERRORE: {exc or "interrotto"}. Le cartelle .partial NON sono backup completati.', file=sys.stderr)
        sys.exit(1)
