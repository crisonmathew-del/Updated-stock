# Nightly database backups (see backup.sh). Built on the database image itself so pg_dump and
# pg_restore always match the server (PostgreSQL 16 + TimescaleDB); rclone (a static binary,
# copied from its official image) mirrors the dumps to S3-compatible storage when configured.
ARG BASE_IMAGE=timescale/timescaledb:2.30.2-pg16
ARG RCLONE_IMAGE=rclone/rclone:1.75.1

FROM ${RCLONE_IMAGE} AS rclone

FROM ${BASE_IMAGE}
COPY --from=rclone /usr/local/bin/rclone /usr/local/bin/rclone
COPY backup.sh /usr/local/bin/breakout-backup
RUN chmod 0755 /usr/local/bin/breakout-backup
ENTRYPOINT ["breakout-backup"]
CMD ["schedule"]
