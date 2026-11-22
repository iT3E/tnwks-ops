# The fake Linux host of the lab of the host server (test_main_host_server.py):
# OpenSSH with one normal user, `agent`, key sign-in only. No key is in the
# image: the test makes new keys each run and mounts them; the start step
# installs them. The same recipe as the fake host of the update check of the
# host approval plugin (iT3E/tnwks-agent-image, tests/host-approval-check/sshd).
# The base by digest. Renovate reads no file under tests/, so a move of it is by
# hand, with the sshd-base pin of the agent image repository.
ARG BASE=alpine:3.20@sha256:d9e853e87e55526f6b2917df91a2115c36dd7c696a35be12163d44e6e2a4b6bc
FROM ${BASE}
RUN apk add --no-cache openssh-server bash coreutils procps \
 && adduser -D -s /bin/bash agent \
 && { passwd -u agent >/dev/null 2>&1 || true; } \
 && install -d -o agent -g agent -m 700 /home/agent/.ssh \
 && echo "root only" > /etc/lab-root-only && chmod 600 /etc/lab-root-only \
 && echo "lab host ok" > /etc/lab-motd && chmod 644 /etc/lab-motd \
 && touch /home/agent/.bashrc /home/agent/.profile \
 && chown root:root /home/agent/.bashrc /home/agent/.profile && chmod 644 /home/agent/.bashrc /home/agent/.profile
COPY sshd_config /etc/ssh/sshd_config
EXPOSE 22
CMD ["sh", "-c", "install -o agent -g agent -m 600 /k/agent.pub /home/agent/.ssh/authorized_keys && install -m 600 /k/host /etc/ssh/ssh_host_ed25519_key && exec /usr/sbin/sshd -D -e"]
