# Include inside api.mhenwa.cc's HTTPS server{} only. No change to /v1 routing.
# The adapter additionally requires its own random Authorization Bearer RPC secret.
location = /_connect/eligible {
    access_log off;
    client_max_body_size 16k;
    proxy_pass http://127.0.0.1:18788/eligible;
    proxy_set_header Authorization $http_authorization;
    proxy_set_header Host $host;
    proxy_connect_timeout 5s;
    proxy_read_timeout 10s;
    proxy_next_upstream off;
}
location = /_connect/introspect {
    access_log off;
    client_max_body_size 16k;
    proxy_pass http://127.0.0.1:18788/introspect;
    proxy_set_header Authorization $http_authorization;
    proxy_set_header Host $host;
    proxy_connect_timeout 5s;
    proxy_read_timeout 10s;
    proxy_next_upstream off;
}
