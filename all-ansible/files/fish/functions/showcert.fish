function showcert
    echo "" | \
        openssl s_client -servername $argv[1] -showcerts -connect $argv[1]:443 \
        | openssl x509 -inform pem -noout -text
end
