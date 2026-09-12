# 安装前只读盘点

首次记录保留在 infra_before.json；该文件在拉取任何镜像前生成。

EXCLUSION LIST：原有 SSH、DNS、chrony、腾讯云安全/自动化代理、Docker daemon、80/22/ICMP 云防火墙规则；没有既有容器、镜像或项目数据。仅操作 DeepHelp 专属资源。

## uname -a

```text
Linux VM-0-17-ubuntu 6.8.0-124-generic #124-Ubuntu SMP PREEMPT_DYNAMIC Tue May 26 13:00:45 UTC 2026 x86_64 x86_64 x86_64 GNU/Linux
```

## cat /etc/os-release

```text
PRETTY_NAME="Ubuntu 24.04.4 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.4 LTS (Noble Numbat)"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
SUPPORT_URL="https://help.ubuntu.com/"
BUG_REPORT_URL="https://bugs.launchpad.net/ubuntu/"
PRIVACY_POLICY_URL="https://www.ubuntu.com/legal/terms-and-policies/privacy-policy"
UBUNTU_CODENAME=noble
LOGO=ubuntu-logo
```

## lscpu

```text
Architecture:                            x86_64
CPU op-mode(s):                          32-bit, 64-bit
Address sizes:                           48 bits physical, 48 bits virtual
Byte Order:                              Little Endian
CPU(s):                                  4
On-line CPU(s) list:                     0-3
Vendor ID:                               AuthenticAMD
BIOS Vendor ID:                          Smdbmds
Model name:                              AMD EPYC 7K62 48-Core Processor
BIOS Model name:                         3.0  CPU @ 2.0GHz
BIOS CPU family:                         1
CPU family:                              23
Model:                                   49
Thread(s) per core:                      1
Core(s) per socket:                      4
Socket(s):                               1
Stepping:                                0
BogoMIPS:                                5190.24
Flags:                                   fpu vme de pse tsc msr pae mce cx8 apic sep mtrr pge mca cmov pat pse36 clflush mmx fxsr sse sse2 ht syscall nx mmxext fxsr_opt pdpe1gb rdtscp lm rep_good nopl cpuid extd_apicid tsc_known_freq pni pclmulqdq ssse3 fma cx16 sse4_1 sse4_2 x2apic movbe popcnt aes xsave avx f16c rdrand hypervisor lahf_lm cmp_legacy cr8_legacy abm sse4a misalignsse 3dnowprefetch osvw topoext ibpb vmmcall fsgsbase bmi1 avx2 smep bmi2 rdseed adx smap clflushopt sha_ni xsaveopt xsavec xgetbv1 arat
Hypervisor vendor:                       KVM
Virtualization type:                     full
L1d cache:                               128 KiB (4 instances)
L1i cache:                               128 KiB (4 instances)
L2 cache:                                16 MiB (4 instances)
L3 cache:                                16 MiB (1 instance)
NUMA node(s):                            1
NUMA node0 CPU(s):                       0-3
Vulnerability Gather data sampling:      Not affected
Vulnerability Indirect target selection: Not affected
Vulnerability Itlb multihit:             Not affected
Vulnerability L1tf:                      Not affected
Vulnerability Mds:                       Not affected
Vulnerability Meltdown:                  Not affected
Vulnerability Mmio stale data:           Not affected
Vulnerability Reg file data sampling:    Not affected
Vulnerability Retbleed:                  Mitigation; untrained return thunk; SMT disabled
Vulnerability Spec rstack overflow:      Vulnerable
Vulnerability Spec store bypass:         Vulnerable
Vulnerability Spectre v1:                Mitigation; usercopy/swapgs barriers and __user pointer sanitization
Vulnerability Spectre v2:                Mitigation; Retpolines; IBPB conditional; STIBP disabled; RSB filling; PBRSB-eIBRS Not affected; BHI Not affected
Vulnerability Srbds:                     Not affected
Vulnerability Tsa:                       Not affected
Vulnerability Tsx async abort:           Not affected
Vulnerability Vmscape:                   Not affected
```

## cat /proc/meminfo

```text
MemTotal:        7869652 kB
MemFree:         6173296 kB
MemAvailable:    7280372 kB
Buffers:          139120 kB
Cached:          1171564 kB
SwapCached:            0 kB
Active:           642492 kB
Inactive:         840392 kB
Active(anon):     183532 kB
Inactive(anon):        0 kB
Active(file):     458960 kB
Inactive(file):   840392 kB
Unevictable:       28820 kB
Mlocked:           27284 kB
SwapTotal:       2035708 kB
SwapFree:        2035708 kB
Zswap:                 0 kB
Zswapped:              0 kB
Dirty:               108 kB
Writeback:             0 kB
AnonPages:        195680 kB
Mapped:           208108 kB
Shmem:              2576 kB
KReclaimable:      57104 kB
Slab:             118404 kB
SReclaimable:      57104 kB
SUnreclaim:        61300 kB
KernelStack:        3632 kB
PageTables:         4788 kB
SecPageTables:         0 kB
NFS_Unstable:          0 kB
Bounce:                0 kB
WritebackTmp:          0 kB
CommitLimit:     5970532 kB
Committed_AS:    1436948 kB
VmallocTotal:   34359738367 kB
VmallocUsed:       17320 kB
VmallocChunk:          0 kB
Percpu:             2528 kB
HardwareCorrupted:     0 kB
AnonHugePages:         0 kB
ShmemHugePages:        0 kB
ShmemPmdMapped:        0 kB
FileHugePages:         0 kB
FilePmdMapped:         0 kB
Unaccepted:            0 kB
HugePages_Total:       0
HugePages_Free:        0
HugePages_Rsvd:        0
HugePages_Surp:        0
Hugepagesize:       2048 kB
Hugetlb:               0 kB
DirectMap4k:      143224 kB
DirectMap2M:     4050944 kB
DirectMap1G:     6291456 kB
```

## free -h

```text
               total        used        free      shared  buff/cache   available
Mem:           7.5Gi       575Mi       5.9Gi       2.5Mi       1.3Gi       6.9Gi
Swap:          1.9Gi          0B       1.9Gi
```

## swapon --show

```text
NAME      TYPE SIZE USED PRIO
/swap.img file 1.9G   0B   -2
```

## df -hT

```text
Filesystem     Type   Size  Used Avail Use% Mounted on
tmpfs          tmpfs  769M 1016K  768M   1% /run
/dev/vda2      ext4    59G  5.7G   51G  11% /
tmpfs          tmpfs  3.8G   24K  3.8G   1% /dev/shm
tmpfs          tmpfs  5.0M     0  5.0M   0% /run/lock
```

## df -i

```text
Filesystem      Inodes  IUsed   IFree IUse% Mounted on
tmpfs           983706    698  983008    1% /run
/dev/vda2      3932160 113674 3818486    3% /
tmpfs           983706      7  983699    1% /dev/shm
tmpfs           983706      3  983703    1% /run/lock
```

## lsblk -f

```text
NAME   FSTYPE  FSVER            LABEL    UUID                                 FSAVAIL FSUSE% MOUNTPOINTS
sr0    iso9660 Joliet Extension config-2 2026-09-11-23-58-29-00                              
vda                                                                                          
├─vda1                                                                                       
└─vda2 ext4    1.0                       9842d3d6-a839-4127-bda7-f19137effe71   50.8G    10% /
```

## findmnt

```text
TARGET                          SOURCE      FSTYPE      OPTIONS
/                               /dev/vda2   ext4        rw,relatime
├─/sys                          sysfs       sysfs       rw,nosuid,nodev,noexec,relatime
│ ├─/sys/kernel/security        securityfs  securityfs  rw,nosuid,nodev,noexec,relatime
│ ├─/sys/fs/cgroup              cgroup2     cgroup2     rw,nosuid,nodev,noexec,relatime,nsdelegate,memory_recursiveprot
│ ├─/sys/fs/pstore              pstore      pstore      rw,nosuid,nodev,noexec,relatime
│ ├─/sys/fs/bpf                 bpf         bpf         rw,nosuid,nodev,noexec,relatime,mode=700
│ ├─/sys/kernel/debug           debugfs     debugfs     rw,nosuid,nodev,noexec,relatime
│ │ └─/sys/kernel/debug/tracing tracefs     tracefs     rw,nosuid,nodev,noexec,relatime
│ ├─/sys/kernel/tracing         tracefs     tracefs     rw,nosuid,nodev,noexec,relatime
│ ├─/sys/fs/fuse/connections    fusectl     fusectl     rw,nosuid,nodev,noexec,relatime
│ └─/sys/kernel/config          configfs    configfs    rw,nosuid,nodev,noexec,relatime
├─/proc                         proc        proc        rw,nosuid,nodev,noexec,relatime
│ └─/proc/sys/fs/binfmt_misc    systemd-1   autofs      rw,relatime,fd=32,pgrp=1,timeout=0,minproto=5,maxproto=5,direct,pipe_ino=437
│   └─/proc/sys/fs/binfmt_misc  binfmt_misc binfmt_misc rw,nosuid,nodev,noexec,relatime
├─/dev                          udev        devtmpfs    rw,nosuid,relatime,size=3893080k,nr_inodes=973270,mode=755,inode64
│ ├─/dev/pts                    devpts      devpts      rw,nosuid,noexec,relatime,gid=5,mode=620,ptmxmode=000
│ ├─/dev/shm                    tmpfs       tmpfs       rw,nosuid,nodev,inode64
│ ├─/dev/hugepages              hugetlbfs   hugetlbfs   rw,nosuid,nodev,relatime,pagesize=2M
│ └─/dev/mqueue                 mqueue      mqueue      rw,nosuid,nodev,noexec,relatime
└─/run                          tmpfs       tmpfs       rw,nosuid,nodev,noexec,relatime,size=786968k,mode=755,inode64
  └─/run/lock                   tmpfs       tmpfs       rw,nosuid,nodev,noexec,relatime,size=5120k,inode64
```

## df -hT /srv /var /var/lib/docker

```text
Filesystem     Type  Size  Used Avail Use% Mounted on
/dev/vda2      ext4   59G  5.7G   51G  11% /
/dev/vda2      ext4   59G  5.7G   51G  11% /
/dev/vda2      ext4   59G  5.7G   51G  11% /
```

## du -sh /var/lib/docker /var/lib/containerd

```text
212K	/var/lib/docker
92K	/var/lib/containerd
```

## docker version

```text
Client: Docker Engine - Community
 Version:           29.6.1
 API version:       1.55
 Go version:        go1.26.4
 Git commit:        8900f1d
 Built:             Fri Jun 26 11:40:19 2026
 OS/Arch:           linux/amd64
 Context:           default

Server: Docker Engine - Community
 Engine:
  Version:          29.6.1
  API version:      1.55 (minimum version 1.40)
  Go version:       go1.26.4
  Git commit:       8ec5ab3
  Built:            Fri Jun 26 11:40:19 2026
  OS/Arch:          linux/amd64
  Experimental:     false
 containerd:
  Version:          v2.2.5
  GitCommit:        e53c7c1516c3b2bff98eb76f1f4117477e6f4e66
 runc:
  Version:          1.3.6
  GitCommit:        v1.3.6-0-g491b69ba
 docker-init:
  Version:          0.19.0
  GitCommit:        de40ad0
```

## docker info

```text
Client: Docker Engine - Community
 Version:    29.6.1
 Context:    default
 Debug Mode: false
 Plugins:
  buildx: Docker Buildx (Docker Inc.)
    Version:  v0.35.0
    Path:     /usr/libexec/docker/cli-plugins/docker-buildx
  compose: Docker Compose (Docker Inc.)
    Version:  v5.3.1
    Path:     /usr/libexec/docker/cli-plugins/docker-compose

Server:
 Containers: 0
  Running: 0
  Paused: 0
  Stopped: 0
 Images: 0
 Server Version: 29.6.1
 Storage Driver: overlayfs
  driver-type: io.containerd.snapshotter.v1
 Logging Driver: json-file
 Cgroup Driver: systemd
 Cgroup Version: 2
 Plugins:
  Volume: local
  Network: bridge host ipvlan macvlan null overlay
  Log: awslogs fluentd gcplogs gelf journald json-file local splunk syslog
 CDI spec directories:
  /etc/cdi
  /var/run/cdi
 Swarm: inactive
 Runtimes: io.containerd.runc.v2 runc
 Default Runtime: runc
 Init Binary: docker-init
 containerd version: e53c7c1516c3b2bff98eb76f1f4117477e6f4e66
 runc version: v1.3.6-0-g491b69ba
 init version: de40ad0
 Security Options:
  apparmor
  seccomp
   Profile: builtin
  cgroupns
 Kernel Version: 6.8.0-124-generic
 Operating System: Ubuntu 24.04.4 LTS
 OSType: linux
 Architecture: x86_64
 CPUs: 4
 Total Memory: 7.505GiB
 Name: VM-0-17-ubuntu
 ID: 0f90c176-9c45-4708-8103-37b5ff2464f1
 Docker Root Dir: /var/lib/docker
 Debug Mode: false
 Experimental: false
 Insecure Registries:
  ::1/128
  127.0.0.0/8
 Registry Mirrors:
  https://mirror.ccs.tencentyun.com/
 Live Restore Enabled: false
 Firewall Backend: iptables
  EnableUserlandProxy: true
  UserlandProxyPath: /usr/bin/docker-proxy

```

## docker compose version

```text
Docker Compose version v5.3.1
```

## docker ps -a --no-trunc

```text
CONTAINER ID   IMAGE     COMMAND   CREATED   STATUS    PORTS     NAMES
```

## docker images --digests

```text
REPOSITORY   TAG       DIGEST    IMAGE ID   CREATED   SIZE
```

## docker volume ls

```text
DRIVER    VOLUME NAME
```

## docker system df

```text
TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE
Images          0         0         0B        0B
Containers      0         0         0B        0B
Local Volumes   0         0         0B        0B
Build Cache     0         0         0B        0B
```

## ss -lntup

```text
Netid State  Recv-Q Send-Q  Local Address:Port Peer Address:PortProcess                                                   
udp   UNCONN 0      0          127.0.0.54:53        0.0.0.0:*    users:(("systemd-resolve",pid=470,fd=16))                
udp   UNCONN 0      0       127.0.0.53%lo:53        0.0.0.0:*    users:(("systemd-resolve",pid=470,fd=14))                
udp   UNCONN 0      0      10.1.0.17%eth0:68        0.0.0.0:*    users:(("systemd-network",pid=1040,fd=21))               
udp   UNCONN 0      0           127.0.0.1:323       0.0.0.0:*    users:(("chronyd",pid=1710,fd=5))                        
udp   UNCONN 0      0               [::1]:323          [::]:*    users:(("chronyd",pid=1710,fd=6))                        
tcp   LISTEN 0      4096          0.0.0.0:22        0.0.0.0:*    users:(("sshd",pid=119489,fd=3),("systemd",pid=1,fd=204))
tcp   LISTEN 0      4096    127.0.0.53%lo:53        0.0.0.0:*    users:(("systemd-resolve",pid=470,fd=15))                
tcp   LISTEN 0      4096       127.0.0.54:53        0.0.0.0:*    users:(("systemd-resolve",pid=470,fd=17))                
tcp   LISTEN 0      4096             [::]:22           [::]:*    users:(("sshd",pid=119489,fd=4),("systemd",pid=1,fd=205))
```

## ufw status verbose

```text
Status: inactive
```

## nft list ruleset

```text
table ip nat {
	chain DOCKER {
	}

	chain PREROUTING {
		type nat hook prerouting priority dstnat; policy accept;
		fib daddr type local counter packets 23605 bytes 670922 jump DOCKER
	}

	chain OUTPUT {
		type nat hook output priority dstnat; policy accept;
		ip daddr != 127.0.0.0/8 fib daddr type local counter packets 0 bytes 0 jump DOCKER
	}

	chain POSTROUTING {
		type nat hook postrouting priority srcnat; policy accept;
		ip saddr 172.17.0.0/16 oifname != "docker0" counter packets 0 bytes 0 masquerade
	}
}
table ip filter {
	chain DOCKER {
		iifname != "docker0" oifname "docker0" counter packets 0 bytes 0 drop
	}

	chain DOCKER-FORWARD {
		counter packets 0 bytes 0 jump DOCKER-CT
		counter packets 0 bytes 0 jump DOCKER-INTERNAL
		counter packets 0 bytes 0 jump DOCKER-BRIDGE
		iifname "docker0" counter packets 0 bytes 0 accept
	}

	chain DOCKER-BRIDGE {
		oifname "docker0" counter packets 0 bytes 0 jump DOCKER
	}

	chain DOCKER-CT {
		oifname "docker0" ct state related,established counter packets 0 bytes 0 accept
	}

	chain DOCKER-INTERNAL {
	}

	chain FORWARD {
		type filter hook forward priority filter; policy accept;
		counter packets 0 bytes 0 jump DOCKER-USER
		counter packets 0 bytes 0 jump DOCKER-FORWARD
	}

	chain DOCKER-USER {
	}

	chain YJ-FIREWALL-INPUT {
		ip saddr 103.204.159.72 counter packets 0 bytes 0 reject
	}

	chain INPUT {
		type filter hook input priority filter; policy accept;
		counter packets 9557 bytes 1397860 jump YJ-FIREWALL-INPUT
	}
}
table ip6 nat {
	chain DOCKER {
	}

	chain PREROUTING {
		type nat hook prerouting priority dstnat; policy accept;
		fib daddr type local counter packets 0 bytes 0 jump DOCKER
	}

	chain OUTPUT {
		type nat hook output priority dstnat; policy accept;
		ip6 daddr != ::1 fib daddr type local counter packets 0 bytes 0 jump DOCKER
	}
}
table ip6 filter {
	chain DOCKER {
	}

	chain DOCKER-FORWARD {
		counter packets 0 bytes 0 jump DOCKER-CT
		counter packets 0 bytes 0 jump DOCKER-INTERNAL
		counter packets 0 bytes 0 jump DOCKER-BRIDGE
	}

	chain DOCKER-BRIDGE {
	}

	chain DOCKER-CT {
	}

	chain DOCKER-INTERNAL {
	}

	chain FORWARD {
		type filter hook forward priority filter; policy accept;
		counter packets 0 bytes 0 jump DOCKER-USER
		counter packets 0 bytes 0 jump DOCKER-FORWARD
	}

	chain DOCKER-USER {
	}
}
# Warning: table ip nat is managed by iptables-nft, do not touch!
# Warning: table ip filter is managed by iptables-nft, do not touch!
# Warning: table ip6 nat is managed by iptables-nft, do not touch!
```

## iptables-save

```text
# Generated by iptables-save v1.8.10 (nf_tables) on Sat Sep 12 09:44:24 2026
*filter
:INPUT ACCEPT [9527:1395572]
:FORWARD ACCEPT [0:0]
:OUTPUT ACCEPT [0:0]
:DOCKER - [0:0]
:DOCKER-BRIDGE - [0:0]
:DOCKER-CT - [0:0]
:DOCKER-FORWARD - [0:0]
:DOCKER-INTERNAL - [0:0]
:DOCKER-USER - [0:0]
:YJ-FIREWALL-INPUT - [0:0]
-A INPUT -j YJ-FIREWALL-INPUT
-A FORWARD -j DOCKER-USER
-A FORWARD -j DOCKER-FORWARD
-A DOCKER ! -i docker0 -o docker0 -j DROP
-A DOCKER-BRIDGE -o docker0 -j DOCKER
-A DOCKER-CT -o docker0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
-A DOCKER-FORWARD -j DOCKER-CT
-A DOCKER-FORWARD -j DOCKER-INTERNAL
-A DOCKER-FORWARD -j DOCKER-BRIDGE
-A DOCKER-FORWARD -i docker0 -j ACCEPT
-A YJ-FIREWALL-INPUT -s 103.204.159.72/32 -j REJECT --reject-with icmp-port-unreachable
COMMIT
# Completed on Sat Sep 12 09:44:24 2026
# Generated by iptables-save v1.8.10 (nf_tables) on Sat Sep 12 09:44:24 2026
*nat
:PREROUTING ACCEPT [23606:670958]
:INPUT ACCEPT [0:0]
:OUTPUT ACCEPT [20307:1258336]
:POSTROUTING ACCEPT [20307:1258336]
:DOCKER - [0:0]
-A PREROUTING -m addrtype --dst-type LOCAL -j DOCKER
-A OUTPUT ! -d 127.0.0.0/8 -m addrtype --dst-type LOCAL -j DOCKER
-A POSTROUTING -s 172.17.0.0/16 ! -o docker0 -j MASQUERADE
COMMIT
# Completed on Sat Sep 12 09:44:24 2026
```

## sshd -T

```text
port 22
addressfamily any
listenaddress [::]:22
listenaddress 0.0.0.0:22
usepam yes
logingracetime 120
x11displayoffset 10
maxauthtries 6
maxsessions 10
clientaliveinterval 0
clientalivecountmax 3
requiredrsasize 1024
streamlocalbindmask 0177
unusedconnectiontimeout none
permitrootlogin yes
ignorerhosts yes
ignoreuserknownhosts no
hostbasedauthentication no
hostbasedusesnamefrompacketonly no
pubkeyauthentication yes
kerberosauthentication no
kerberosorlocalpasswd yes
kerberosticketcleanup yes
gssapiauthentication no
gssapicleanupcredentials yes
gssapikeyexchange no
gssapistrictacceptorcheck yes
gssapistorecredentialsonrekey no
gssapikexalgorithms gss-group14-sha256-,gss-group16-sha512-,gss-nistp256-sha256-,gss-curve25519-sha256-,gss-group14-sha1-,gss-gex-sha1-
passwordauthentication no
kbdinteractiveauthentication no
printmotd no
printlastlog yes
x11forwarding yes
x11uselocalhost yes
permittty yes
permituserrc yes
strictmodes yes
tcpkeepalive yes
permitemptypasswords no
compression yes
gatewayports no
usedns no
allowtcpforwarding yes
allowagentforwarding yes
disableforwarding no
allowstreamlocalforwarding yes
streamlocalbindunlink no
fingerprinthash SHA256
exposeauthinfo no
debianbanner yes
pidfile /run/sshd.pid
modulifile /etc/ssh/moduli
xauthlocation /usr/bin/xauth
ciphers chacha20-poly1305@openssh.com,aes128-ctr,aes192-ctr,aes256-ctr,aes128-gcm@openssh.com,aes256-gcm@openssh.com
macs umac-64-etm@openssh.com,umac-128-etm@openssh.com,hmac-sha2-256-etm@openssh.com,hmac-sha2-512-etm@openssh.com,hmac-sha1-etm@openssh.com,umac-64@openssh.com,umac-128@openssh.com,hmac-sha2-256,hmac-sha2-512,hmac-sha1
banner none
forcecommand none
chrootdirectory none
trustedusercakeys none
revokedkeys none
securitykeyprovider internal
authorizedprincipalsfile none
versionaddendum none
authorizedkeyscommand none
authorizedkeyscommanduser none
authorizedprincipalscommand none
authorizedprincipalscommanduser none
hostkeyagent none
kexalgorithms sntrup761x25519-sha512@openssh.com,curve25519-sha256,curve25519-sha256@libssh.org,ecdh-sha2-nistp256,ecdh-sha2-nistp384,ecdh-sha2-nistp521,diffie-hellman-group-exchange-sha256,diffie-hellman-group16-sha512,diffie-hellman-group18-sha512,diffie-hellman-group14-sha256
casignaturealgorithms ssh-ed25519,ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,ecdsa-sha2-nistp521,sk-ssh-ed25519@openssh.com,sk-ecdsa-sha2-nistp256@openssh.com,rsa-sha2-512,rsa-sha2-256
hostbasedacceptedalgorithms ssh-ed25519-cert-v01@openssh.com,ecdsa-sha2-nistp256-cert-v01@openssh.com,ecdsa-sha2-nistp384-cert-v01@openssh.com,ecdsa-sha2-nistp521-cert-v01@openssh.com,sk-ssh-ed25519-cert-v01@openssh.com,sk-ecdsa-sha2-nistp256-cert-v01@openssh.com,rsa-sha2-512-cert-v01@openssh.com,rsa-sha2-256-cert-v01@openssh.com,ssh-ed25519,ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,ecdsa-sha2-nistp521,sk-ssh-ed25519@openssh.com,sk-ecdsa-sha2-nistp256@openssh.com,rsa-sha2-512,rsa-sha2-256
hostkeyalgorithms ssh-ed25519-cert-v01@openssh.com,ecdsa-sha2-nistp256-cert-v01@openssh.com,ecdsa-sha2-nistp384-cert-v01@openssh.com,ecdsa-sha2-nistp521-cert-v01@openssh.com,sk-ssh-ed25519-cert-v01@openssh.com,sk-ecdsa-sha2-nistp256-cert-v01@openssh.com,rsa-sha2-512-cert-v01@openssh.com,rsa-sha2-256-cert-v01@openssh.com,ssh-ed25519,ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,ecdsa-sha2-nistp521,sk-ssh-ed25519@openssh.com,sk-ecdsa-sha2-nistp256@openssh.com,rsa-sha2-512,rsa-sha2-256
pubkeyacceptedalgorithms ssh-ed25519-cert-v01@openssh.com,ecdsa-sha2-nistp256-cert-v01@openssh.com,ecdsa-sha2-nistp384-cert-v01@openssh.com,ecdsa-sha2-nistp521-cert-v01@openssh.com,sk-ssh-ed25519-cert-v01@openssh.com,sk-ecdsa-sha2-nistp256-cert-v01@openssh.com,rsa-sha2-512-cert-v01@openssh.com,rsa-sha2-256-cert-v01@openssh.com,ssh-ed25519,ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,ecdsa-sha2-nistp521,sk-ssh-ed25519@openssh.com,sk-ecdsa-sha2-nistp256@openssh.com,rsa-sha2-512,rsa-sha2-256
loglevel INFO
syslogfacility AUTH
authorizedkeysfile .ssh/authorized_keys .ssh/authorized_keys2
hostkey /etc/ssh/ssh_host_rsa_key
hostkey /etc/ssh/ssh_host_ecdsa_key
hostkey /etc/ssh/ssh_host_ed25519_key
acceptenv LANG
acceptenv LC_*
authenticationmethods any
channeltimeout none
subsystem sftp /usr/lib/openssh/sftp-server 
maxstartups 10:30:100
persourcemaxstartups none
persourcenetblocksize 32:128
permittunnel no
ipqos lowdelay throughput
rekeylimit 0 0
permitopen any
permitlisten any
permituserenvironment no
pubkeyauthoptions none
```

## systemctl is-enabled docker

```text
enabled
```

## systemctl cat containerd

```text
# /usr/lib/systemd/system/containerd.service
# Copyright The containerd Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

[Unit]
Description=containerd container runtime
Documentation=https://containerd.io
After=network.target dbus.service

[Service]
ExecStartPre=-/sbin/modprobe overlay
ExecStart=/usr/bin/containerd

Type=notify
Delegate=yes
KillMode=process
Restart=always
RestartSec=5

# Having non-zero Limit*s causes performance problems due to accounting overhead
# in the kernel. We recommend using cgroups to do container-local accounting.
LimitNPROC=infinity
LimitCORE=infinity

# Comment TasksMax if your systemd version does not supports it.
# Only systemd 226 and above support this version.
TasksMax=infinity
OOMScoreAdjust=-999

[Install]
WantedBy=multi-user.target
```

## ls -la /srv

```text
total 8
drwxr-xr-x  2 root root 4096 Apr 23  2024 .
drwxr-xr-x 24 root root 4096 Sep 12 09:44 ..
```
