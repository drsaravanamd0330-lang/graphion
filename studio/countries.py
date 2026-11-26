"""Country and region names an affiliation line may end with.

Used only to say whether the last segment of an affiliation *looks like* a
country. A segment not on the list is kept as written and flagged for the
editor; it is never replaced or corrected.
"""

COUNTRIES = {
    "afghanistan", "albania", "algeria", "andorra", "angola", "argentina", "armenia", "australia",
    "austria", "azerbaijan", "bahamas", "bahrain", "bangladesh", "barbados", "belarus", "belgium",
    "belize", "benin", "bhutan", "bolivia", "bosnia and herzegovina", "botswana", "brazil", "brunei",
    "bulgaria", "burkina faso", "burundi", "cambodia", "cameroon", "canada", "cape verde",
    "central african republic", "chad", "chile", "china", "colombia", "comoros", "congo",
    "democratic republic of the congo", "costa rica", "côte d'ivoire", "cote d'ivoire", "croatia", "cuba",
    "cyprus", "czech republic", "czechia", "denmark", "djibouti", "dominica", "dominican republic",
    "ecuador", "egypt", "el salvador", "equatorial guinea", "eritrea", "estonia", "eswatini", "ethiopia",
    "fiji", "finland", "france", "gabon", "gambia", "georgia", "germany", "ghana", "greece", "grenada",
    "guatemala", "guinea", "guinea-bissau", "guyana", "haiti", "honduras", "hong kong", "hungary",
    "iceland", "india", "indonesia", "iran", "iraq", "ireland", "israel", "italy", "jamaica", "japan",
    "jordan", "kazakhstan", "kenya", "kiribati", "kosovo", "kuwait", "kyrgyzstan", "laos", "latvia",
    "lebanon", "lesotho", "liberia", "libya", "liechtenstein", "lithuania", "luxembourg", "macau",
    "madagascar", "malawi", "malaysia", "maldives", "mali", "malta", "mauritania", "mauritius", "mexico",
    "moldova", "monaco", "mongolia", "montenegro", "morocco", "mozambique", "myanmar", "namibia",
    "nauru", "nepal", "netherlands", "the netherlands", "new zealand", "nicaragua", "niger", "nigeria",
    "north korea", "north macedonia", "norway", "oman", "pakistan", "palau", "palestine", "panama",
    "papua new guinea", "paraguay", "peru", "philippines", "poland", "portugal", "puerto rico", "qatar",
    "romania", "russia", "russian federation", "rwanda", "saint lucia", "samoa", "san marino",
    "saudi arabia", "senegal", "serbia", "seychelles", "sierra leone", "singapore", "slovakia",
    "slovenia", "solomon islands", "somalia", "south africa", "south korea", "republic of korea",
    "korea", "south sudan", "spain", "sri lanka", "sudan", "suriname", "sweden", "switzerland", "syria",
    "taiwan", "tajikistan", "tanzania", "thailand", "timor-leste", "togo", "tonga",
    "trinidad and tobago", "tunisia", "turkey", "türkiye", "turkmenistan", "tuvalu", "uganda",
    "ukraine", "united arab emirates", "uae", "united kingdom", "uk", "united states", "usa",
    "united states of america", "uruguay", "uzbekistan", "vanuatu", "vatican city", "venezuela",
    "vietnam", "viet nam", "yemen", "zambia", "zimbabwe",
}


def looks_like_country(segment: str) -> bool:
    return segment.strip().strip(".").lower() in COUNTRIES
