from dataclasses import dataclass

HERO_COUNT = 1
FEATURED_COUNT = 2


@dataclass(frozen=True)
class DesignSystem:
    colors: dict[str, str]
    typography: dict[str, str]
    spacing: dict[str, int]
    radii: dict[str, int]
    shadows: dict[str, str]
    role_scales: dict[str, tuple[float, float]]

    def css_variables(self) -> str:
        tokens = {f'--{name}': value for name, value in self.colors.items()}
        tokens.update({f'--font-{name}': value for name, value in self.typography.items()})
        tokens.update({f'--space-{name}': f'{value}px' for name, value in self.spacing.items()})
        tokens.update({f'--radius-{name}': f'{value}px' for name, value in self.radii.items()})
        tokens.update({f'--shadow-{name}': value for name, value in self.shadows.items()})
        return ':root{' + ';'.join(f'{name}:{value}' for name, value in tokens.items()) + '}'


DESIGN_SYSTEM = DesignSystem(
    colors={
        'brand-primary': '#ffd326',
        'brand-primary-light': '#ffed87',
        'brand-secondary': '#0d4437',
        'brand-ink': '#102e30',
        'promo-accent': '#b82222',
        'promo-accent-dark': '#851d1b',
        'surface': '#fffdf0',
        'surface-soft': '#fff9e4',
        'text': '#102e30',
        'text-muted': '#626b65',
        'on-dark': '#ffffff',
        'price': '#ffe66b',
        'line-subtle': 'rgba(16,46,48,.08)',
        'line-strong': 'rgba(255,255,255,.75)',
        'line-footer': 'rgba(16,46,48,.15)',
    },
    typography={'body': "Arial, sans-serif", 'display': "Arial Black, Arial, sans-serif"},
    spacing={'xs': 4, 'sm': 8, 'md': 12, 'lg': 20, 'xl': 28},
    radii={'card': 16, 'pill': 22, 'small': 8},
    shadows={
        'card': '0 6px 16px rgba(16,46,48,.14)',
        'hero': '0 12px 30px rgba(16,46,48,.20)',
        'image': '0 5px 6px rgba(16,46,48,.16)',
    },
    # Scales are bounded in DesignSpecV2 and shared by every template.
    role_scales={
        'hero': (1.18, 1.28),
        'featured': (1.08, 1.12),
        'standard': (1.0, 1.0),
        'compact': (.90, 1.05),
    },
)
