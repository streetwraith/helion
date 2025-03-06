from django_cron import CronJobBase, Schedule

class UpdateMarketData(CronJobBase):
    RUN_EVERY_MINS = 1  # every minute

    schedule = Schedule(run_every_mins=RUN_EVERY_MINS)
    code = 'market.update_market_data'    # a unique code

    def do(self):
        print("Cron Job running")