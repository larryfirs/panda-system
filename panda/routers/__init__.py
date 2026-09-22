from . import (
    config_,
    cron,
    dashboard,
    dependence,
    env,
    health,
    log,
    record,
    script,
    system,
    user,
)

all_routers = [
    health.router,
    user.router,
    cron.router,
    script.router,
    log.router,
    record.router,
    dependence.router,
    env.router,
    config_.router,
    system.router,
    dashboard.router,
]
