#!/bin/bash
set -a
source <(grep -E '^ALCHEMY_API_KEY=' /Users/wangshaoyu/.hermes/profiles/dev-claude/.env)
set +a
for net in eth-mainnet arb-mainnet base-mainnet bnb-mainnet unichain-mainnet; do
  echo "== $net =="
  curl -s -o /tmp/alchemy_probe_body.json -w "HTTP %{http_code}\n" \
    -X POST "https://${net}.g.alchemy.com/v2/${ALCHEMY_API_KEY}" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}'
  cat /tmp/alchemy_probe_body.json
  echo
done
