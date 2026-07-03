# ssl-cert-expiry

Checks certificate expiry dates for configured domains by calling `openssl s_client` and `openssl x509`.

## Config

- `domains`: domains to inspect on port 443
- `warn_if_expiring_within_days`: warning threshold
- `critical_if_expiring_within_days`: critical threshold, default `7`

## Usage

The script reports expiry dates and returns `1` if any certificate is within the warning or critical window, `2` on execution errors, otherwise `0`. Output is sent to Telegram when configured, else stdout.
