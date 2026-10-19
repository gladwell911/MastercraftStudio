#!/bin/bash
# Root-owned Certbot deploy hook. No remote operations; only mc-relay units.
set -Eeuo pipefail
umask 077
fail() { echo "mc-relay certificate deployment: $*" >&2; exit 1; }
[[ $EUID == 0 ]] || fail 'root required'
lineage=/etc/mc-relay/acme/live/mc-relay
[[ ${RENEWED_LINEAGE:-} == "$lineage" ]] || fail 'unexpected RENEWED_LINEAGE'
[[ -d $lineage && -f $lineage/fullchain.pem && -f $lineage/privkey.pem ]] || fail 'missing lineage files'
exec 9>/etc/mc-relay/.deploy-cert.lock
flock -n 9 || fail 'another certificate deployment is running'
mapfile -t domains < /etc/mc-relay/cert-domains
[[ ${#domains[@]} == 4 ]] || fail 'configure exactly four final SAN domains'
for domain in "${domains[@]}"; do
    [[ $domain =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ && $domain == *.* ]] || fail 'invalid domain configuration'
done
[[ $(printf '%s\n' "${domains[@]}" | sort -u | wc -l) == 4 ]] || fail 'duplicate domains'
tls=/etc/mc-relay/tls
[[ -d $tls && ! -L $tls ]] || fail 'missing or symlinked TLS directory'
work=$(mktemp -d "$tls/.deploy.XXXXXXXX")
changed=0
had_old=0
caddy_active=0
frps_active=0
cleanup() { rm -rf -- "$work"; }
recover() {
    local result=$?
    trap - ERR HUP INT TERM
    if (( changed )); then
        if (( had_old )); then
            install -o root -g mc-relay -m 0640 "$work/old-fullchain.pem" "$tls/fullchain.pem" || result=1
            install -o root -g mc-relay -m 0640 "$work/old-privkey.pem" "$tls/privkey.pem" || result=1
            if (( caddy_active )); then
                runuser -u mc-relay -- /opt/mc-relay/caddy validate --config /etc/mc-relay/Caddyfile --adapter caddyfile && systemctl reload mc-relay-caddy.service || result=1
            fi
            if (( frps_active )); then systemctl restart mc-relay-frps.service || result=1; fi
            echo 'Restored previous relay certificate pair; check mc-relay unit status.' >&2
        else
            rm -f -- "$tls/fullchain.pem" "$tls/privkey.pem"
            echo 'Removed failed initial certificate installation.' >&2
        fi
    fi
    exit "${result:-1}"
}
trap cleanup EXIT
trap recover ERR
trap 'false' HUP INT TERM
# Copy once, then validate and deploy these same bytes.
install -o root -g mc-relay -m 0640 "$lineage/fullchain.pem" "$work/fullchain.pem"
install -o root -g mc-relay -m 0640 "$lineage/privkey.pem" "$work/privkey.pem"
openssl x509 -in "$work/fullchain.pem" -out "$work/leaf.pem"
awk '/-----BEGIN CERTIFICATE-----/ {n++} n>1 {print}' "$work/fullchain.pem" > "$work/chain.pem"
[[ -s $work/chain.pem ]] || fail 'fullchain must include intermediates'
openssl verify -CAfile /etc/ssl/certs/ca-certificates.crt -untrusted "$work/chain.pem" -purpose sslserver "$work/leaf.pem"
openssl x509 -in "$work/leaf.pem" -checkend 86400 -noout
openssl x509 -in "$work/leaf.pem" -ext subjectAltName | tr ',' '\n' | sed -n 's/^[[:space:]]*DNS://p' > "$work/sans"
for domain in "${domains[@]}"; do grep -Fxiq -- "$domain" "$work/sans" || fail 'required SAN missing'; done
openssl x509 -in "$work/leaf.pem" -pubkey -noout | openssl pkey -pubin -outform DER > "$work/cert-public.der"
openssl pkey -in "$work/privkey.pem" -pubout -outform DER > "$work/key-public.der"
cmp -s "$work/cert-public.der" "$work/key-public.der" || fail 'certificate/key mismatch'
if [[ -e $tls/fullchain.pem || -e $tls/privkey.pem ]]; then
    [[ -f $tls/fullchain.pem && -f $tls/privkey.pem && ! -L $tls/fullchain.pem && ! -L $tls/privkey.pem ]] || fail 'incomplete or symlinked current pair'
    cp -- "$tls/fullchain.pem" "$work/old-fullchain.pem"
    cp -- "$tls/privkey.pem" "$work/old-privkey.pem"
    had_old=1
    # Keep a restricted, paired backup beyond the lifetime of this process.
    backup=$(mktemp -d "$tls/previous.XXXXXXXX")
    install -o root -g root -m 0600 "$work/old-fullchain.pem" "$backup/fullchain.pem"
    install -o root -g root -m 0600 "$work/old-privkey.pem" "$backup/privkey.pem"
fi
if systemctl is-active --quiet mc-relay-caddy.service; then caddy_active=1; fi
if systemctl is-active --quiet mc-relay-frps.service; then frps_active=1; fi
[[ $had_old == 1 || ( $caddy_active == 0 && $frps_active == 0 ) ]] || fail 'active service without previous pair'
changed=1
mv -f -- "$work/fullchain.pem" "$tls/fullchain.pem"
mv -f -- "$work/privkey.pem" "$tls/privkey.pem"
runuser -u mc-relay -- /opt/mc-relay/caddy validate --config /etc/mc-relay/Caddyfile --adapter caddyfile
if (( caddy_active )); then systemctl reload mc-relay-caddy.service; fi
if (( frps_active )); then systemctl restart mc-relay-frps.service; fi
changed=0
echo 'MC relay certificate installed; previously active relay services refreshed.'
