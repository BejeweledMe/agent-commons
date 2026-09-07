import type { StarterPack } from "./contracts";
import type { MessageKey } from "./i18n";

type Text = (key: MessageKey) => string;

/** Localize verified bundled display metadata; canonical identities and roles stay intact. */
export function starterPackPresentation(pack: StarterPack, text: Text): StarterPack {
  if (pack.sourceKind !== "bundled" || pack.version !== "0.1.0" || pack.example !== true) return pack;
  const feature = pack.id === "starter.feature-delivery.mock";
  const discovery = pack.id === "starter.product-discovery.mock";
  if (!feature && !discovery) return pack;
  const roles: Readonly<Record<string, readonly [MessageKey, MessageKey]>> = feature ? {
    implementer: ["recipe_implementer_name", "recipe_implementer_purpose"],
    "independent-reviewer": ["recipe_independent_reviewer_name", "recipe_independent_reviewer_purpose"]
  } : {
    researcher: ["recipe_researcher_name", "recipe_researcher_purpose"],
    "product-reviewer": ["recipe_product_reviewer_name", "recipe_product_reviewer_purpose"]
  };
  return {
    ...pack,
    title: text(feature ? "recipe_feature_title" : "recipe_discovery_title"),
    summary: text(feature ? "recipe_feature_summary" : "recipe_discovery_summary"),
    blueprints: pack.blueprints.map((blueprint) => {
      if (blueprint.id !== (feature ? "feature-delivery" : "product-discovery")) return blueprint;
      return {
        ...blueprint,
        title: text(feature ? "recipe_feature_title" : "recipe_discovery_title"),
        summary: text(feature ? "recipe_feature_blueprint_summary" : "recipe_discovery_blueprint_summary"),
        roles: blueprint.roles.map((role) => {
          const keys = roles[role.id];
          return keys === undefined ? role : { ...role, name: text(keys[0]), purpose: text(keys[1]) };
        })
      };
    })
  };
}
