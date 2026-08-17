from esi.openapi_clients import ESIClientProvider

# The client is filtered to the operations helion calls; loading the full
# spec into pydantic models costs ~90 MB of memory.
esi = ESIClientProvider(
    compatibility_date="2026-08-04",
    ua_appname="Helion",
    ua_version="1.0.0",
    ua_url="https://helion.entropiadev.com",
    operations=[
        "GetCharactersCharacterIdWalletTransactions",
        "GetCharactersCharacterIdWalletJournal",
        "GetCharactersCharacterIdOrders",
        "GetCharactersCharacterIdAssets",
        "PostCharactersCharacterIdAssetsNames",
        "GetCharactersCharacterIdContracts",
        "GetCharactersCharacterIdSkills",
        "GetCharactersCharacterIdSkillqueue",
        "GetCharactersCharacterIdAttributes",
        "GetCharactersCharacterIdClones",
        "GetCharactersCharacterIdImplants",
        "PostUniverseNames",
        "GetUniverseStructuresStructureId",
        "GetMarketsRegionIdOrders",
        "GetMarketsRegionIdHistory",
        "GetLoyaltyStoresCorporationIdOffers",
        "PostRoute",
        "PostUiOpenwindowMarketdetails",
    ],
)
