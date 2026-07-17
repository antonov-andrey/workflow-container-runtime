FROM python:3.14-trixie

ENV NPM_CONFIG_UPDATE_NOTIFIER=false \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git gnupg jq ripgrep \
    && curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g npm@11.18.0 @openai/codex@0.144.1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=workflow_container_contract README.md pyproject.toml /tmp/workflow-container-contract/
COPY --from=workflow_container_contract workflow_container_contract /tmp/workflow-container-contract/workflow_container_contract
COPY README.md pyproject.toml /tmp/workflow-container-runtime/
COPY workflow_container_runtime /tmp/workflow-container-runtime/workflow_container_runtime

RUN python -m pip install --root-user-action=ignore --no-cache-dir \
        /tmp/workflow-container-contract \
        /tmp/workflow-container-runtime \
    && rm -rf /tmp/workflow-container-contract /tmp/workflow-container-runtime \
    && groupadd --gid 1000 workflow \
    && useradd --create-home --gid 1000 --shell /usr/sbin/nologin --uid 1000 workflow \
    && mkdir -p /app/workflow /input /result /runtime /workspace \
    && chown -R 1000:1000 /app/workflow /result /runtime /workspace

WORKDIR /app/workflow
USER 1000:1000
