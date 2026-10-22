"""
push_new_cards_to_mongo.py
──────────────────────────
Pushes the 41 new cards (16 Bleach + 25 Academy rarity) into MongoDB.
Run this ONCE on your server or locally:
    python3 push_new_cards_to_mongo.py

Requirements: pymongo  (pip install pymongo)
Set MONGO_URI below or as an environment variable.
"""

import os
from pymongo import MongoClient, UpdateOne

# ── Config ────────────────────────────────────────────────────────────────────
MONGO_URI = os.environ.get(
    "MONGO_URI",
    "mongodb+srv://nishanthofficial11_db_user:nishanthse28@cluster0.luqxd0f.mongodb.net/?appName=Cluster0"
)
DB_NAME = "naruto_tcg"
# ─────────────────────────────────────────────────────────────────────────────

NEW_CARDS = [
    {"id": 1164, "name": "hinata Hyuga", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqYGonBnSAil4qaKy2iM5JEgbyOSnqAAI-DmsbJ9o5VbByK7MveaVfAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1165, "name": "Sakura Haruno", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqZWonBthCz3liF74S4-5_a2ctnczOAAI2EWsbcLQoVY50VQPFhPIRAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1166, "name": "Temari", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqaGonBvM1mlTx4KcXhVKpxm4m3a-kAAJADmsbJ9o5VeCOdpfiL7J0AQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1167, "name": "Tsunade Senju", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqa2onBwlTjsX26WjCZOBNS5LSHWWXAAJBDmsbJ9o5VTDxVrvBZo5uAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1168, "name": "Konan", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqbmonByqItXYnxSR6DwZu6zuYMKAeAALtEGsbzLkwVedQNKhFMXLSAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1169, "name": "Kushina Uzumaki", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqcWonB1E4zhBZVWbkeeRp8_8URJ61AAJCDmsbJ9o5VXGqmpkbZnIpAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1170, "name": "Ino Yamanaka", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqdGonB2RQuQY_YIFGoMCXgUmdf4CuAAJDDmsbJ9o5VR3ilyQ_7yHPAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1171, "name": "Karin Uzumaki", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqd2onB6TaBnhMtBrS-o6IqIWO_32iAAJEDmsbJ9o5VbclIAb65vVQAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1172, "name": "Mikoto Uchiha", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqemonB8dz1bhSijROtQABY0XyjGY3FwACRQ5rGyfaOVUi5drrjQjjCAEAAwIAA3cAAzsE", "file_type": "photo", "series": "Naruto"},
    {"id": 1173, "name": "Mei Terumi", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqfWonB98uKxTximSqnlRTd9PfZ7ERAAJGDmsbJ9o5VeR6Vw0oLQ66AQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1174, "name": "Hanabi Hyuga", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqgGonB_ReNrpKwgk3PrYnRBG_Q8JpAAJHDmsbJ9o5VTIs1M2vMtKrAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1175, "name": "Yugao Uzuki", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqg2onCAwPIXCZtCqkJ7oL6WKz2ODaAAJIDmsbJ9o5VRYdah-P1DN-AQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1176, "name": "Samui", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqhmonCCdpo1mcv93fYqc4dU5ydLw-AAJJDmsbJ9o5VUeN8ZbMN4upAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1177, "name": "TenTen", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqiWonCDlMq-iC-Oi5WRg3c6it8QE-AAJKDmsbJ9o5VQiMnv05pWPRAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1178, "name": "Kurenai Yuhi", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqjGonCExLypcvRcU-0687zWOvVaavAAJMDmsbJ9o5VWy1cV3VG_mPAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1179, "name": "Naruto Uzumaki", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqj2onCGdvnd2ltkjfRSjN6Po4Zt9YAAJNDmsbJ9o5Vfnam2hupqlkAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1180, "name": "Sasuke Uchiha", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqkmonCIBKjW8CjMYZcbvOpIE007i_AAJODmsbJ9o5VZi6l52-abTSAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1181, "name": "Kakashi Hatake", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqlWonCJBSb19rEt2LNfSTrjKYaoCDAAJPDmsbJ9o5VV0FZmD5ToEBAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1182, "name": "Boruto Uzumaki", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqmGonCKP0Z4vMQOfLH4zeN6Tkq5Z_AAJQDmsbJ9o5VbzAeiAponsvAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1183, "name": "Shikamaru Nara", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqm2onCLMgr0MKAd040MTElstgLCULAAJRDmsbJ9o5VdNNTFqem_EgAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1184, "name": "Minato Namikaze", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqnmonCMTRHLpuRF_M57pv9VbhkEoEAAJSDmsbJ9o5VZTSc_FKvhMTAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1185, "name": "Itachi Uchiha", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqoWonCNUC8bK34w7V75m5v9H3UihYAAJTDmsbJ9o5VVDXG8JOa70RAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1186, "name": "Madara Uchiha", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqpGonCOKOV48cAAFJ9dtxhAABzl704KcAAlQOaxsn2jlViNiB194_IVUBAAMCAAN3AAM7BA", "file_type": "photo", "series": "Naruto"},
    {"id": 1187, "name": "Pain", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqp2onCPMZSe2E4OqBiUzCcLQtZpwNAAJWDmsbJ9o5VR-f8ucVGpSKAQADAgADdwADOwQ", "file_type": "photo", "series": "Naruto"},
    {"id": 1188, "name": "Gaara", "rarity": "Academy", "file_id": "AgACAgUAAxkBAAJqqmonCQABR1gReHGGHWtUOo0qUi4ivAACVw5rGyfaOVVJkQmaKCMgvwEAAwIAA3cAAzsE", "file_type": "photo", "series": "Naruto"},
    # Bleach cards
    {"id": 1189, "name": "Rukia Kuchiki", "rarity": "Summer", "file_id": "AgACAgUAAxkBAAJqr2onCrAqkhXmNhpLDaWPOHDJ3-F8AAINEWsbJ3gRVbWeZWfZ0-4EAQADAgADdwADOwQ", "file_type": "photo", "series": "Bleach"},
    {"id": 1190, "name": "Orihime Inoue", "rarity": "Summer", "file_id": "AgACAgUAAxkBAAJqsmonCtww04PTHdTAm4m7FTj4_cReAAIOEWsbJ3gRVYqs0RaWjx2oAQADAgADdwADOwQ", "file_type": "photo", "series": "Bleach"},
    {"id": 1191, "name": "Yoruichi Shihoin", "rarity": "Summer", "file_id": "AgACAgUAAxkBAAJqtWonCxzyZLjXAUQOQeTOnEt8R0eRAAIPEWsbJ3gRVcmIipU1tl1iAQADAgADdwADOwQ", "file_type": "photo", "series": "Bleach"},
    {"id": 1192, "name": "Yachiru Unohana", "rarity": "Summer", "file_id": "AgACAgUAAxkBAAJquWonC1MRykRAZrR97lOwq51n2NcIAAIQEWsbJ3gRVVcCkjjgy7IHAQADAgADdwADOwQ", "file_type": "photo", "series": "Bleach"},
    {"id": 1193, "name": "Nelliel Tu Odelachwanck", "rarity": "Summer", "file_id": "AgACAgUAAxkBAAJqvGonC3HDarX0PvxIONLCzDGecTVJAAIREWsbJ3gRVQcfW7BykWh9AQADAgADdwADOwQ", "file_type": "photo", "series": "Bleach"},
    {"id": 1194, "name": "Tier Harribel", "rarity": "Summer", "file_id": "AgACAgUAAxkBAAJqv2onC5y7jcBzJGd1KyFPkNcuF4syAAISEWsbJ3gRVeIuVqB81UYqAQADAgADdwADOwQ", "file_type": "photo", "series": "Bleach"},
    {"id": 1195, "name": "Soi Fon", "rarity": "Summer", "file_id": "AgACAgUAAxkBAAJqxGonC7uC8Eeczg7Qj2XxDaexcPpXAALVEWsbJ3gRVZdjr6EUGChVAQADAgADdwADOwQ", "file_type": "photo", "series": "Bleach"},
    {"id": 1196, "name": "Nemu kurotsuchi", "rarity": "Summer", "file_id": "AgACAgUAAxkBAAJqx2onC9D7LZPHXZI63m5LULsp5lyzAALWEWsbJ3gRVTrNqE2IAnZ8AQADAgADdwADOwQ", "file_type": "photo", "series": "Bleach"},
    {"id": 1197, "name": "Rangiku Matsumoto", "rarity": "Summer", "file_id": "AgACAgUAAxkBAAJqymonC-JqUBUK-RMhAuA_iuIdh-8wAALXEWsbJ3gRVUJ6kw3lvgSPAQADAgADdwADOwQ", "file_type": "photo", "series": "Bleach"},
    {"id": 1198, "name": "Nanao Ise", "rarity": "Summer", "file_id": "AgACAgUAAxkBAAJqzWonDAHIOf1wh7E7cFLqMmboF2SgAALYEWsbJ3gRVT_KigZUSS0oAQADAgADdwADOwQ", "file_type": "photo", "series": "Bleach"},
    {"id": 1199, "name": "Ichigo Kurosaki", "rarity": "Summer", "file_id": "AgACAgUAAxkBAAJq0GonDCGfCqct-SZc_BwBWdsP718_AAJcDmsbJ9o5VSG0JqV_nkz4AQADAgADdwADOwQ", "file_type": "photo", "series": "Bleach"},
    {"id": 1200, "name": "Byakuya Kuchiki", "rarity": "Summer", "file_id": "AgACAgUAAxkBAAJq1GonDDQW9800Ff8qLq164G6sxG_8AAJdDmsbJ9o5VSaAWq38FahPAQADAgADdwADOwQ", "file_type": "photo", "series": "Bleach"},
    {"id": 1201, "name": "Sosuke Aizen", "rarity": "Summer", "file_id": "AgACAgUAAxkBAAJq12onDEq28bBln0Ev59ceYedCT-pxAAJeDmsbJ9o5VUN5cAyDC_mLAQADAgADdwADOwQ", "file_type": "photo", "series": "Bleach"},
    {"id": 1202, "name": "Kisuke Urahara", "rarity": "Summer", "file_id": "AgACAgUAAxkBAAJq2monDGA2Z18m18AopTaqi6lAvM_xAAJfDmsbJ9o5Vfs78edqCiG4AQADAgADdwADOwQ", "file_type": "photo", "series": "Bleach"},
    {"id": 1203, "name": "Girmmjow Jaegerjaquez", "rarity": "Summer", "file_id": "AgACAgUAAxkBAAJq3WonDHEgCk8U5sMv3TSL2tdSqfk4AAJgDmsbJ9o5VZysVK2s9cBqAQADAgADdwADOwQ", "file_type": "photo", "series": "Bleach"},
    {"id": 1204, "name": "Uryu Ishida", "rarity": "Summer", "file_id": "AgACAgUAAxkBAAJq4GonDI5b0uGR-Yz0bpTWXH7UhDroAAJhDmsbJ9o5VSKFSnCmVg4oAQADAgADdwADOwQ", "file_type": "photo", "series": "Bleach"},
]


def main():
    print(f"[PUSH] Connecting to MongoDB...")
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=10000)
    client.admin.command("ping")
    db = client[DB_NAME]
    print(f"[PUSH] ✅ Connected to: {DB_NAME}")

    ops = []
    for card in NEW_CARDS:
        ops.append(UpdateOne(
            {"id": card["id"]},
            {"$set": card},
            upsert=True
        ))

    result = db.cards.bulk_write(ops, ordered=False)
    print(f"\n[PUSH] ✅ Done!")
    print(f"  Inserted : {result.upserted_count}")
    print(f"  Updated  : {result.modified_count}")
    print(f"  Total    : {len(NEW_CARDS)} cards pushed (25 Academy + 16 Bleach)")
    client.close()


if __name__ == "__main__":
    main()
