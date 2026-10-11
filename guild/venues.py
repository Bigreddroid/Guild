"""Guild's demo venue catalogue, indexed into Elasticsearch for the Researcher.

Invented venues, labelled as demo data wherever they appear. It mixes cities,
sizes and prices so the search has to filter (Lisbon, seats 6+) and rank
(cheapest first) to get the right shortlist.
"""
INDEX = "guild-venues"

VENUES = [
    {"id": "lis-tejo-loft", "name": "Tejo Riverside Loft", "city": "lisbon", "neighbourhood": "Alcântara",
     "capacity": 12, "price_per_day": 420, "amenities": ["projector", "whiteboard", "kitchen"]},
    {"id": "lis-alfama-studio", "name": "Alfama Courtyard Studio", "city": "lisbon", "neighbourhood": "Alfama",
     "capacity": 8, "price_per_day": 310, "amenities": ["screen", "whiteboard"]},
    {"id": "lis-chiado-room", "name": "Chiado Meeting Room", "city": "lisbon", "neighbourhood": "Chiado",
     "capacity": 10, "price_per_day": 380, "amenities": ["projector", "coffee"]},
    {"id": "lis-graca-attic", "name": "Graça Attic", "city": "lisbon", "neighbourhood": "Graça",
     "capacity": 4, "price_per_day": 150, "amenities": ["screen"]},
    {"id": "lis-belem-hall", "name": "Belém Garden Hall", "city": "lisbon", "neighbourhood": "Belém",
     "capacity": 40, "price_per_day": 1200, "amenities": ["stage", "projector", "catering"]},
    {"id": "lis-principe-salon", "name": "Príncipe Real Salon", "city": "lisbon", "neighbourhood": "Príncipe Real",
     "capacity": 6, "price_per_day": 340, "amenities": ["screen", "terrace"]},
    {"id": "opo-ribeira-room", "name": "Ribeira Workshop", "city": "porto", "neighbourhood": "Ribeira",
     "capacity": 10, "price_per_day": 220, "amenities": ["projector"]},
    {"id": "opo-bolhao-loft", "name": "Bolhão Loft", "city": "porto", "neighbourhood": "Bolhão",
     "capacity": 8, "price_per_day": 190, "amenities": ["whiteboard"]},
    {"id": "mad-malasana-space", "name": "Malasaña Space", "city": "madrid", "neighbourhood": "Malasaña",
     "capacity": 12, "price_per_day": 260, "amenities": ["projector", "kitchen"]},
]

MAPPINGS = {"properties": {
    "name": {"type": "text"}, "city": {"type": "keyword"}, "neighbourhood": {"type": "keyword"},
    "capacity": {"type": "integer"}, "price_per_day": {"type": "integer"}, "amenities": {"type": "keyword"},
}}
