# Nightly encrypted Postgres backups (see deploy/README.md, "Backups").
FROM postgres:16-alpine
RUN apk add --no-cache age rclone
COPY backup.sh run.sh /usr/local/bin/
CMD ["run.sh"]
