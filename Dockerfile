FROM debian:trixie

# do not use alpine because we need standard coreutils like date
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl logrotate bash unzip 7zip python3 iproute2 \
    && rm -rf /var/cache/apt/* \
    && rm -rf /var/lib/apt/lists/* \
    && (curl https://gosspublic.alicdn.com/ossutil/install.sh | bash)

# Latest releases available at https://github.com/aptible/supercronic/releases
ENV SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.45/supercronic-linux-amd64 \
    SUPERCRONIC_SHA1SUM=e894b193bea75a5ee644e700c59e30eedc804cf7 \
    SUPERCRONIC=supercronic-linux-amd64

RUN curl -fsSLO "$SUPERCRONIC_URL" \
 && echo "${SUPERCRONIC_SHA1SUM}  ${SUPERCRONIC}" | sha1sum -c - \
 && chmod +x "$SUPERCRONIC" \
 && mv "$SUPERCRONIC" "/usr/local/bin/${SUPERCRONIC}" \
 && ln -s "/usr/local/bin/${SUPERCRONIC}" /usr/local/bin/supercronic

WORKDIR /app

COPY auto_dns.py get_ip_via_policy_routing.py docker/run-ddns-once.sh /app/

RUN chmod +x /app/auto_dns.py /app/get_ip_via_policy_routing.py /app/run-ddns-once.sh \
 && mkdir -p /data/cron \
 && mkdir -p /logs \
 && echo '* * * * * /app/run-ddns-once.sh' > /data/cron/crontab

CMD ["supercronic", "-inotify", "/data/cron/crontab"]
