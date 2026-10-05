# Include in the codex.mhenwa.cc HTTPS server{}. Keep existing TLS/ACME settings.
location / {
    # Pairing secrets live in fragments, but no chat/device paths need access logs.
    access_log off;
    client_max_body_size 20m;
    proxy_pass http://127.0.0.1:18790;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    # Do not trust caller-supplied XFF or a browser tenant/device selector.
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto https;
    proxy_buffering off;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
    proxy_next_upstream off;
}
