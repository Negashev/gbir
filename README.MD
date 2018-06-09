# gbir

Gitlab bot image cleaner (work with [gric](https://github.com/Negashev/gric))

Remove old images (7 days default) from gitlab registry ignore develop, master, latest, release*

### install

1. Add user (bot) with admin privileges

2. run bot!

    `GBIR_CLEAN_TOKEN`: gric CLEAN_TOKEN
    
    `GBIR_TOKEN`: gitlab token api, read registry
    
    `GBIR_CLEAN_URL`: gric service url
    
    `GBIR_FREEZE_TIME`: time in second for old images (default 604800 = 7 days)
    
    `GBIR_DELETE_SIZE`: how many images remove every `GBIR_SECONDS_DELETE_TAGS` seconds (default 10)
    
    `GBIR_DELETE_TIMEOUT`: timeout of gric request (default 600)
    
    `GBIR_REGISTRY_URL`: registry hostname with port if need (default empty)

    
    ```bash
    docker run -it \
    --name gbir \
    --restart=always \
    -d \
    -e GBIR_CLEAN_TOKEN='mysecret key' \
    -e GBIR_TOKEN='mysecret token' \
    -e GBIR_URL='https://gitlab.my.company' \
    -e GBIR_CLEAN_URL='https://gric.gitlab.my.company' \
    -e GBIR_REGISTRY_URL='docker.gitlab.my.company:1234' \
    negash/gbir
    ```