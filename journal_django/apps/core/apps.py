from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'apps.core'
    label = 'core'
    verbose_name = 'Core'

    def ready(self) -> None:
        # node-postgres returns jsonb columns as parsed objects; psycopg2 over a
        # raw cursor returns them as strings by default. Register the jsonb
        # typecaster on every DB connection as it is created, so all raw
        # repositories get dicts/lists — keeping responses byte-identical with
        # Express. (We use managed=False models with raw SQL, never ORM
        # JSONField, so this is safe.)
        from django.db.backends.signals import connection_created

        def _register_jsonb(sender, connection, **kwargs):
            if connection.vendor == 'postgresql':
                from psycopg2.extras import register_default_jsonb
                register_default_jsonb(connection.connection, globally=False)

        connection_created.connect(_register_jsonb, dispatch_uid='core.jsonb')
