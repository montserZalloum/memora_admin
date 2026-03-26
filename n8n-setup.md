# N8N Public File Server Setup Guide                                                                                                    
                                                                                                                                             
## Purpose
Set up a public file server that allows n8n workflows to write files to a shared directory, which are then served publicly via HTTPS    
through an nginx container behind a host-level nginx reverse proxy with Let's Encrypt SSL.

## Architecture
```
n8n container ──writes──> /mnt/shared_files (container path)
                            │
                            ▼
            /opt/n8n_public_files (host path, shared volume)
                            │
                            ▼
        nginx:alpine container (read-only mount, serves files on port 8082)
                            │
                            ▼
        host nginx reverse proxy (SSL termination, port 443)
                            │
                            ▼
        https://<FILESERVER_DOMAIN>/<filename>
```

## Prerequisites
- A Linux server with Docker installed (`docker.io` and `docker-compose-plugin`)
- A host-level nginx installed and running as a reverse proxy
- Certbot installed for Let's Encrypt SSL certificates
- An existing n8n container running via `docker run`
- DNS A record for `<FILESERVER_DOMAIN>` pointing to the server's IP (must be configured BEFORE running certbot)

## Variables to Replace
- `<FILESERVER_DOMAIN>`: The domain for the file server (e.g., `n8n-files.skrterak.com`)
- `<CERTBOT_EMAIL>`: Email for Let's Encrypt registration (e.g., `admin@skrterak.com`)
- `<FILESERVER_PORT>`: Host port for the nginx file server container (we used `8082`)
- `<N8N_CONTAINER_NAME>`: Name of the n8n container (we used `n8n`)

## Step-by-Step Instructions

### Step 1: Create the Shared Directory

```bash
sudo mkdir -p /opt/n8n_public_files
sudo chmod 777 /opt/n8n_public_files
```

### Step 2: Create and Start the File Server Container

Create the file `~/fileserver/docker-compose.yml`:

```yaml
services:
fileserver:
    image: 'nginx:alpine'
    container_name: fileserver
    restart: unless-stopped
    volumes:
    - '/opt/n8n_public_files:/usr/share/nginx/html:ro'
    ports:
    - '<FILESERVER_PORT>:80'
```

Start it:

```bash
cd ~/fileserver
sudo docker compose up -d
```

### Step 3: Recreate the n8n Container with Shared Volume and Env Var

First, inspect the existing n8n container to capture its full configuration:

```bash
sudo docker inspect <N8N_CONTAINER_NAME> --format '{{json .Config.Env}}' | python3 -m json.tool
sudo docker inspect <N8N_CONTAINER_NAME> --format '{{json .Mounts}}' | python3 -m json.tool
sudo docker inspect <N8N_CONTAINER_NAME> --format '{{json .HostConfig.PortBindings}}' | python3 -m json.tool
sudo docker inspect <N8N_CONTAINER_NAME> --format '{{.HostConfig.RestartPolicy.Name}}'
sudo docker inspect <N8N_CONTAINER_NAME> --format '{{.Config.Image}}'
sudo docker inspect <N8N_CONTAINER_NAME> --format '{{.HostConfig.NetworkMode}}'
```

Then stop and remove the existing container:

```bash
sudo docker stop <N8N_CONTAINER_NAME>
sudo docker rm <N8N_CONTAINER_NAME>
```

Recreate it with ALL the original flags plus these two additions:
- Volume: `-v /opt/n8n_public_files:/mnt/shared_files`
- Env var: `-e "N8N_RESTRICT_FILE_ACCESS_TO=/home/node/.n8n;/mnt/shared_files"`

Example (adapt all `-e` and `-v` flags to match what `docker inspect` returned):

```bash
sudo docker run -d \
--name <N8N_CONTAINER_NAME> \
--restart unless-stopped \
-p <ORIGINAL_HOST_PORT>:<ORIGINAL_CONTAINER_PORT> \
-e NODE_ENV=production \
-e N8N_HOST=<ORIGINAL_N8N_HOST> \
-e N8N_PORT=<ORIGINAL_N8N_PORT> \
-e N8N_PROTOCOL=<ORIGINAL_N8N_PROTOCOL> \
-e WEBHOOK_URL=<ORIGINAL_WEBHOOK_URL> \
-e "N8N_RESTRICT_FILE_ACCESS_TO=/home/node/.n8n;/mnt/shared_files" \
-v <ORIGINAL_DATA_VOLUME>:/home/node/.n8n \
-v /opt/n8n_public_files:/mnt/shared_files \
n8nio/n8n:latest
```

IMPORTANT: The host data directory (e.g., `/opt/n8n/data`) persists on disk, so no data is lost when recreating the container. Always
verify with `docker inspect` after recreation that all mounts and env vars are correct.

### Step 4: Set Up Host Nginx Reverse Proxy with SSL

#### 4a. Create a temporary HTTP-only config for the certbot challenge

Write to `/etc/nginx/sites-enabled/<FILESERVER_DOMAIN>-temp.conf`:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name <FILESERVER_DOMAIN>;

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}
```

Test and reload nginx:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

#### 4b. Obtain the SSL certificate

```bash
sudo certbot certonly --webroot -w /var/www/html -d <FILESERVER_DOMAIN> --non-interactive --agree-tos --email <CERTBOT_EMAIL>
```

#### 4c. Replace the temp config with the full HTTPS config

Remove the temp config and write to `/etc/nginx/sites-enabled/<FILESERVER_DOMAIN>.conf`:

```bash
sudo rm /etc/nginx/sites-enabled/<FILESERVER_DOMAIN>-temp.conf
```

```nginx
upstream n8n-files {
    server 127.0.0.1:<FILESERVER_PORT> fail_timeout=0;
}

server {
    listen 80;
    listen [::]:80;
    server_name <FILESERVER_DOMAIN>;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;

    server_name <FILESERVER_DOMAIN>;

    ssl_certificate /etc/letsencrypt/live/<FILESERVER_DOMAIN>/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/<FILESERVER_DOMAIN>/privkey.pem;
    ssl_trusted_certificate /etc/letsencrypt/live/<FILESERVER_DOMAIN>/chain.pem;

    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers
ECDHE-RSA-AES256-GCM-SHA512:DHE-RSA-AES256-GCM-SHA512:ECDHE-RSA-AES256-GCM-SHA384:DHE-RSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-SHA384;
    ssl_prefer_server_ciphers on;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;

    client_max_body_size 50m;

    location / {
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_pass http://n8n-files;
        proxy_redirect off;
    }

    access_log /var/log/nginx/n8n-files-access.log main;
    error_log  /var/log/nginx/n8n-files-error.log;

    sendfile on;
    keepalive_timeout 15;
    gzip on;
    gzip_http_version 1.1;
    gzip_comp_level 5;
    gzip_min_length 256;
    gzip_proxied any;
    gzip_vary on;
    gzip_types
        application/atom+xml
        application/javascript
        application/json
        application/rss+xml
        application/vnd.ms-fontobject
        application/x-font-ttf
        application/font-woff
        application/x-web-app-manifest+json
        application/xhtml+xml
        application/xml
        font/opentype
        image/svg+xml
        image/x-icon
        text/css
        text/plain
        text/x-component
        ;
}
```

Test and reload:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

### Step 5: Verify

```bash
# Test file creation
echo "hello from fileserver" | sudo tee /opt/n8n_public_files/test.txt

# Test HTTPS access
curl -s https://<FILESERVER_DOMAIN>/test.txt
# Expected output: hello from fileserver

# Verify n8n container has the new volume and env var
sudo docker inspect <N8N_CONTAINER_NAME> --format '{{json .Mounts}}' | python3 -m json.tool
sudo docker inspect <N8N_CONTAINER_NAME> --format '{{json .Config.Env}}' | python3 -m json.tool

# Clean up test file
sudo rm /opt/n8n_public_files/test.txt
```

## How n8n Uses This
- In n8n workflows, write files to the path `/mnt/shared_files/<filename>`
- Those files become publicly accessible at `https://<FILESERVER_DOMAIN>/<filename>`
- The `N8N_RESTRICT_FILE_ACCESS_TO` env var permits n8n to read/write both its data dir and the shared files dir