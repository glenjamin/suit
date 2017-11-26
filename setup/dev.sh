mkdir -p ~/Development
mkdir -p ~/Development/GitHub

# Install a json2hcl via bash script
curl -SsL https://github.com/kvz/json2hcl/releases/download/v0.0.6/json2hcl_v0.0.6_darwin_amd64 \
  | sudo tee /usr/local/bin/json2hcl > /dev/null && sudo chmod 755 /usr/local/bin/json2hcl

# Enable locate database
sudo launchctl load -w /System/Library/LaunchDaemons/com.apple.locate.plist
