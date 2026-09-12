#!/usr/bin/env bash
set -euo pipefail
base=$(cd -- "$(dirname -- "$0")/.." && pwd)
cfg="$base/config/connections.env"
[[ -f "$cfg" ]] || cfg="$base/config/connections.env.example"
# Read data without sourcing placeholder values or executing config text.
while IFS='=' read -r k v; do
  [[ "$k" =~ ^(DEEPHELP_SSH_HOST|DEEPHELP_SSH_PORT|DEEPHELP_SSH_USER|DEEPHELP_SSH_KEY_WINDOWS|MYSQL_PORT|REDIS_PORT|MILVUS_LOCAL_PORT|MILVUS_WEBUI_PORT)$ ]] && printf -v "$k" '%s' "${v%$'\r'}"
done < "$cfg"
action=${1:-start};pidfile="$base/client/.tunnel-posix.pid"
managed_pid(){
 [[ -f "$pidfile" ]] || return 1
 local p; p=$(cat "$pidfile"); [[ "$p" =~ ^[0-9]+$ ]] || return 1
 ps -p "$p" -o args= | grep -F -- "$DEEPHELP_SSH_USER@$DEEPHELP_SSH_HOST" | grep -q -- '-N' || return 1
 printf '%s' "$p"
}
if [[ "$action" == stop || "$action" == restart ]];then
 if p=$(managed_pid);then kill "$p";fi
 rm -f -- "$pidfile"
 [[ "$action" == restart ]] || exit 0
fi
ports=("$MYSQL_PORT" "$REDIS_PORT" "$MILVUS_LOCAL_PORT" "$MILVUS_WEBUI_PORT")
if [[ "$action" == check || "$action" == health ]];then
 for p in "${ports[@]}"; do timeout 2 bash -c "</dev/tcp/127.0.0.1/$p"; echo "127.0.0.1:$p reachable";done
 if [[ "$action" == health ]];then "${DEEPHELP_PYTHON:-python3}" "$base/client/test-connections.py" health;fi
 exit 0
fi
if managed_pid >/dev/null;then echo 'Managed tunnel already running';exit 0;fi
for p in "${ports[@]}";do if ss -H -lnt "sport = :$p" | grep -q .;then echo "Local port $p occupied; edit config/connections.env" >&2;exit 1;fi;done
key=${DEEPHELP_SSH_KEY:-$HOME/.ssh/deephelp_lighthouse.pem}
if [[ ! -f "$key" ]] && command -v wslpath >/dev/null;then
 source_key=$(wslpath "$DEEPHELP_SSH_KEY_WINDOWS")
 if [[ -f "$source_key" ]];then install -d -m 700 "$HOME/.ssh";install -m 600 "$source_key" "$key";fi
fi
[[ -f "$key" ]] || { echo "Copy existing private key to $key and chmod 600 it" >&2;exit 1; }
args=(-N -T -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$base/client/known_hosts" -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ConnectTimeout=12 -i "$key" -p "$DEEPHELP_SSH_PORT")
remote=(3306 6379 19530 9091)
for i in 0 1 2 3;do args+=(-L "127.0.0.1:${ports[$i]}:127.0.0.1:${remote[$i]}");done
nohup ssh "${args[@]}" "$DEEPHELP_SSH_USER@$DEEPHELP_SSH_HOST" > "$base/client/.tunnel-posix.log" 2>&1 < /dev/null &
echo $! > "$pidfile"
echo 'Tunnel started; run tunnel.sh health to verify.'
