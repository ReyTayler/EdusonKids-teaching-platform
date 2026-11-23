from rest_framework import serializers


class ArchiveQuerySerializer(serializers.Serializer):
    q = serializers.CharField(required=False, default='', allow_blank=True, max_length=200)
    entity_type = serializers.ChoiceField(
        choices=['all', 'teacher', 'group', 'direction'], required=False, default='all',
    )
