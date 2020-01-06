# -*- coding: utf-8 -*-
# Generated by Django 1.9.13 on 2019-12-30 20:52
from __future__ import unicode_literals

import os
from datetime import timedelta, datetime

from django.db import migrations

from sentry import options


def backfill_eventstream(apps, schema_editor):
    """
    Inserts Postgres events into the eventstream if there are recent events in Postgres.

    This is for open source users migrating from 9.x who want to keep their events.
    If there are no recent events in Postgres, skip the backfill.
    """
    from sentry import eventstore, eventstream
    from sentry.utils.query import RangeQuerySetWrapper

    Event = apps.get_model('sentry', 'Event')
    Group = apps.get_model('sentry', 'Group')
    Project = apps.get_model('sentry', 'Project')

    # Kill switch to skip this migration
    skip_backfill = os.environ.get("SENTRY_SKIP_EVENTS_BACKFILL_FOR_10", False)

    # Use 90 day retention if the option has not been set or set to 0
    DEFAULT_RETENTION = 90
    retention_days = options.get("system.event-retention-days") or DEFAULT_RETENTION

    def get_events(last_days):
        to_date = datetime.now()
        from_date = to_date - timedelta(days=last_days)
        return Event.objects.filter(datetime__gte=from_date, datetime__lte=to_date, group_id__isnull=False)

    def _attach_related(_events):
        project_ids = {event.project_id for event in _events}
        projects = {p.id: p for p in Project.objects.filter(id__in=project_ids)}
        group_ids = {event.group_id for event in _events}
        groups = {g.id: g for g in Group.objects.filter(id__in=group_ids)}
        for event in _events:
            event.project = projects[event.project_id]
            event.group = groups[event.group_id]
        eventstore.bind_nodes(_events, "data")

    if skip_backfill:
        print("Skipping backfill\n")
        return

    events = get_events(retention_days)
    count = events.count()

    if count == 0:
        print("Nothing to do, skipping migration.\n")
        return

    print("Events to process: {}\n".format(count))

    for event in RangeQuerySetWrapper(events, step=100, callbacks=(_attach_related,)):
        primary_hash = event.get_primary_hash()
        eventstream.insert(
            group=event.group,
            event=event,
            is_new=False,
            is_regression=False,
            is_new_group_environment=False,
            primary_hash=primary_hash,
            skip_consume=True,
        )

    print("Done.\n")


class Migration(migrations.Migration):
    # This flag is used to mark that a migration shouldn't be automatically run in
    # production. We set this to True for operations that we think are risky and want
    # someone from ops to run manually and monitor.
    # General advice is that if in doubt, mark your migration as `is_dangerous`.
    # Some things you should always mark as dangerous:
    # - Adding indexes to large tables. These indexes should be created concurrently,
    #   unfortunately we can't run migrations outside of a transaction until Django
    #   1.10. So until then these should be run manually.
    # - Large data migrations. Typically we want these to be run manually by ops so that
    #   they can be monitored. Since data migrations will now hold a transaction open
    #   this is even more important.
    # - Adding columns to highly active tables, even ones that are NULL.
    is_dangerous = True


    dependencies = [
        ('sentry', '0023_hide_environment_none_20191126'),
    ]

    operations = [
        migrations.RunPython(backfill_eventstream, reverse_code=migrations.RunPython.noop),
    ]
