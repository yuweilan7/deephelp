import datetime
import json
import pathlib
import subprocess

cmds = ['uname -a', 'cat /etc/os-release', 'lscpu', 'cat /proc/meminfo', 'free -h', 'swapon --show', 'df -hT', 'df -i', 'lsblk -f', 'findmnt', 'df -hT /srv /var /var/lib/docker', 'du -sh /var/lib/docker /var/lib/containerd', 'docker version', 'docker info', 'docker compose version', 'docker ps -a --no-trunc', 'docker images --digests', 'docker volume ls', 'docker system df', 'ss -lntup', 'ufw status verbose', 'nft list ruleset', 'iptables-save', 'sshd -T', 'systemctl is-enabled docker', 'systemctl cat containerd', 'ls -la /srv']
r = {}
for c in cmds:
    p = subprocess.run(c, shell=True, text=True, capture_output=True, timeout=60)
    r[c] = {'exit': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
r['captured_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
r['cloud'] = {'instance_id': 'lhins-56mgod8p', 'public_ip': '134.175.142.187', 'public_dns': None, 'region': 'ap-guangzhou', 'firewall_inbound': ['TCP 22 all IPv4', 'TCP 80 all IPv4', 'ICMP all IPv4'], 'source': 'Chrome cloud console verified'}
b = pathlib.Path('/srv/deephelp-infra')
(b / 'reports').mkdir(parents=True, exist_ok=True)
p = b / 'reports/infra_before.json'
assert not p.exists(), 'Preserve existing baseline'
p.write_text(json.dumps(r, indent=2))
(b / 'reports/infra_inventory.md').write_text('# Read-only baseline\n\nEXCLUSION LIST: existing SSH, DNS, chrony, Tencent agents, Docker daemon, TCP 80 firewall rule. No pre-existing Docker containers.\n\n' + ''.join('\n## ' + c + '\n\n```text\n' + v['stdout'] + v['stderr'] + '```\n' for c, v in r.items() if isinstance(v, dict) and 'stdout' in v))
print('INVENTORY_SAVED', p)
print(r['docker ps -a --no-trunc']['stdout'])
print(r['free -h']['stdout'])
print(r['df -hT /srv /var /var/lib/docker']['stdout'])
