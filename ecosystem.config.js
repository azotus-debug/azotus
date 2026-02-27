const fs = require("fs");
const { spawnSync } = require("child_process");
const path = require("path");

const ROOT = __dirname;
function isExecutable(candidate) {
  if (!candidate || !candidate.includes("/")) {
    return false;
  }
  try {
    fs.accessSync(candidate, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function hasRuntimeDeps(candidate) {
  if (!candidate) {
    return false;
  }
  const probe = "import importlib.util,sys;mods=('fastapi','uvicorn','vertexai');sys.exit(0 if all(importlib.util.find_spec(m) for m in mods) else 1)";
  const result = spawnSync(candidate, ["-c", probe], { stdio: "ignore" });
  return result.status === 0;
}

function pickPython() {
  let fallback = null;
  const candidates = [
    process.env.OMEGA_PYTHON,
    process.env.OMEGA_VENV_PY,
    path.join(ROOT, ".venv", "bin", "python3"),
    "/opt/homebrew/bin/python3.12",
    "/opt/homebrew/bin/python3.11",
    "/usr/local/bin/python3.12",
    "/usr/local/bin/python3.11",
    "/usr/local/bin/python3.10",
    "/usr/bin/python3",
  ];
  for (const candidate of candidates) {
    if (isExecutable(candidate)) {
      if (!fallback) {
        fallback = candidate;
      }
      if (hasRuntimeDeps(candidate)) {
        return candidate;
      }
    }
  }
  if (fallback) {
    return fallback;
  }
  return "python3";
}

const PYTHON = pickPython();
const FASTAPI_HOST = process.env.OMEGA_FASTAPI_HOST || "127.0.0.1";
const FASTAPI_PORT = process.env.OMEGA_FASTAPI_PORT || "8001";
const FRONTEND_PORT = process.env.OMEGA_FRONTEND_PORT || "3000";
const CLOUD_SQL_INSTANCE =
  process.env.OMEGA_CLOUD_SQL_INSTANCE ||
  "sermon-translator-system:us-central1:omega-sql-prod";
const CLOUD_SQL_PORT = process.env.OMEGA_PG_PORT || "5432";

module.exports = {
  apps: [
    {
      name: "omega-cloud-sql-proxy",
      cwd: ROOT,
      script: "/bin/zsh",
      args: [
        "-lc",
        `if [ "\${DB_TYPE:-postgres}" != "postgres" ]; then exec tail -f /dev/null; fi; if command -v cloud-sql-proxy >/dev/null 2>&1; then exec cloud-sql-proxy "${CLOUD_SQL_INSTANCE}" --port="${CLOUD_SQL_PORT}"; else echo "cloud-sql-proxy not found"; exit 1; fi`,
      ],
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      restart_delay: 2000,
      out_file: path.join(ROOT, "logs", "cloud_sql_proxy.out.log"),
      error_file: path.join(ROOT, "logs", "cloud_sql_proxy.err.log"),
    },
    {
      name: "omega-fastapi",
      cwd: ROOT,
      script: PYTHON,
      args: [
        "-m",
        "uvicorn",
        "api_main:socket_app",
        "--host", FASTAPI_HOST,
        "--port", FASTAPI_PORT,
        "--workers",
        "1",
        "--log-level",
        "info",
      ],
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      restart_delay: 2000,
      kill_timeout: 5000,
      max_memory_restart: "1G",
      out_file: path.join(ROOT, "logs", "fastapi.out.log"),
      error_file: path.join(ROOT, "logs", "fastapi.err.log"),
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
    {
      name: "omega-manager",
      cwd: ROOT,
      script: PYTHON,
      args: ["omega_manager.py"],
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      restart_delay: 2000,
      kill_timeout: 5000,
      max_memory_restart: "1G",
      out_file: path.join(ROOT, "logs", "manager.out.log"),
      error_file: path.join(ROOT, "logs", "manager.err.log"),
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
    {
      name: "omega-cloud-sync",
      cwd: ROOT,
      script: "/bin/zsh",
      args: [
        "-lc",
        `if [ "\${OMEGA_CLOUD_SYNC_ENABLED:-1}" = "1" ] || [ "\${OMEGA_CLOUD_SYNC_ENABLED:-1}" = "true" ] || [ "\${OMEGA_CLOUD_SYNC_ENABLED:-1}" = "yes" ] || [ "\${OMEGA_CLOUD_SYNC_ENABLED:-1}" = "on" ]; then exec "${PYTHON}" cloud_sync_service.py; else exec tail -f /dev/null; fi`,
      ],
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      restart_delay: 2000,
      kill_timeout: 5000,
      max_memory_restart: "1G",
      out_file: path.join(ROOT, "logs", "cloud_sync.out.log"),
      error_file: path.join(ROOT, "logs", "cloud_sync.err.log"),
      env: {
        PYTHONUNBUFFERED: "1",
      },
    },
    {
      name: "omega-frontend",
      cwd: path.join(ROOT, "omega-frontend"),
      script: "/bin/zsh",
      args: [
        "-lc",
        "if [ ! -f .next/standalone/server.js ]; then echo 'missing standalone server build (.next/standalone/server.js)'; exit 1; fi; if [ ! -d .next/static ]; then echo 'missing frontend static build (.next/static)'; exit 1; fi; if [ ! -d .next/standalone/.next/static ]; then mkdir -p .next/standalone/.next/static && cp -R .next/static/. .next/standalone/.next/static/; fi; if [ ! -d .next/standalone/public ] && [ -d public ]; then mkdir -p .next/standalone/public && cp -R public/. .next/standalone/public/; fi; HOSTNAME=127.0.0.1 PORT=${PORT:-3000} exec node .next/standalone/server.js",
      ],
      interpreter: "none",
      autorestart: true,
      max_restarts: 20,
      restart_delay: 2000,
      out_file: path.join(ROOT, "logs", "frontend.out.log"),
      error_file: path.join(ROOT, "logs", "frontend.err.log"),
      env: {
        PORT: FRONTEND_PORT,
      },
    },
  ],
};
