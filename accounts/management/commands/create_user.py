from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from accounts.models import UserProfile
from accounts.roles import TIER_KEYS


class Command(BaseCommand):
    help = ('Create or update a login with a role. Examples:\n'
            '  create_user boss secret123 --role master --name "Owner"\n'
            '  create_user ravi pass1234 --role cashier --tiers retail,reseller --name Ravi')

    def add_arguments(self, p):
        p.add_argument('username')
        p.add_argument('password')
        p.add_argument('--role', choices=['master', 'admin', 'operator', 'cashier'], default='cashier')
        p.add_argument('--tiers', default='retail', help='cashier only: comma list of ' + ', '.join(TIER_KEYS))
        p.add_argument('--name', default='')

    def handle(self, *a, **o):
        if len(o['password']) < 4:
            raise CommandError('Password must be at least 4 characters.')
        from accounts.roles import normalize_tier
        tiers = list(dict.fromkeys(normalize_tier(t.strip()) for t in o['tiers'].split(',') if normalize_tier(t.strip()))) or ['retail']
        user, created = get_user_model().objects.get_or_create(username=o['username'].strip())
        user.set_password(o['password'])
        user.is_active = True
        if o['name']:
            user.first_name = o['name']
        user.is_staff = o['role'] in ('master', 'admin')
        user.is_superuser = o['role'] == 'master'
        user.save()
        prof, _ = UserProfile.objects.get_or_create(user=user)
        prof.role = o['role']
        if o['role'] == 'cashier':
            prof.price_tiers, prof.default_tier = ','.join(tiers), tiers[0]
        prof.save()
        self.stdout.write(self.style.SUCCESS(f'{o["role"]} "{user.username}" {"created" if created else "updated"}.'))
