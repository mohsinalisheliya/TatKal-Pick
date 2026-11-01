from django.db import migrations


def normalize_legacy_tiers(apps, schema_editor):
    Profile = apps.get_model('accounts', 'UserProfile')
    for profile in Profile.objects.all().iterator():
        tiers = []
        for tier in (profile.price_tiers or '').split(','):
            tier = tier.strip()
            if tier in ('retail', 'reseller'):
                tier = 'reselling'
            if tier and tier not in tiers:
                tiers.append(tier)
        profile.price_tiers = ','.join(tiers)
        if profile.default_tier in ('retail', 'reseller'):
            profile.default_tier = 'reselling'
        profile.save(update_fields=['price_tiers', 'default_tier'])


class Migration(migrations.Migration):
    dependencies = [('accounts', '0002_userprofile_can_void_userprofile_phone_and_more')]

    operations = [migrations.RunPython(normalize_legacy_tiers, migrations.RunPython.noop)]
