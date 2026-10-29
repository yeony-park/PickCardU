"""Descriptive paired statistics for correlated, source-authored questions."""
import math

def wilson(successes: int, total: int) -> dict:
    if not isinstance(total, int) or not isinstance(successes, int) or total <= 0 or not 0 <= successes <= total:
        raise ValueError('Invalid binomial denominator')
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    half = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return {'lower': max(0, center - half), 'upper': min(1, center + half), 'n': total, 'confidence': .95,
            'limitation': 'Nominal binomial interval; shared cards/templates violate independent population sampling.'}

def paired_exact(wins: int, losses: int) -> float:
    if not all(isinstance(value, int) and value >= 0 for value in [wins, losses]):
        raise ValueError('Invalid paired counts')
    total = wins + losses
    return min(1, 2 * sum(math.comb(total, k) for k in range(min(wins, losses) + 1)) / 2 ** total) if total else 1
