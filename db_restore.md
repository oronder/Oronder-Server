Download the backup, transfer it to the docker host, then connect to it
```bash
cd .\Downloads\
wsl bash -c 'scp $(ls -1t *tar.gz | head -n1) docker:~/db_backup.tar.gz'
wsl ssh docker
```
From the docker host, transfer it to the db container, and then connect to it
```bash
docker cp ~/db_backup.tar.gz oronder-db:/
docker exec -it oronder-db bash
```
From the docker container, restore the backup
```bash
set PGPASSWORD="$POSTGRES_PASSWORD"
gunzip -c db_backup.tar.gz | pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

After you've started the container, open a psql shell with:
```bash
docker exec -it oronder-db bash -c 'psql -U postgres -d postgres
```
and then run
```psql
CREATE DATABASE fastapi_user;
```