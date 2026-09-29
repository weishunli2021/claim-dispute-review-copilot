"""Builds the knowledge graph from validated DataStore/Pydantic objects.

This module never reads data/*.json directly -- it only consumes the
already-loaded, already-validated records exposed by
tools.data_store.DataStore. Building the graph is deterministic (node ids
are derived only from stable domain ids -- see the *_node_id functions
below) and idempotent: rebuilding from the same DataStore always produces
the same nodes, edges, and properties, with no duplication, since
NetworkX node/edge storage is itself keyed by id (add_node/add_edge with
an id already present in the graph updates that item's attributes rather
than creating a second copy).

NetworkX, not a production graph platform: NetworkX is a pure-Python,
in-memory graph library with no server, no persistence, and no query
language of its own -- exactly right for a prototype this size (dozens of
nodes) where the goal is to demonstrate graph modeling and bounded-depth
traversal without standing up external infrastructure. It has no
transactions, no concurrent-access story, no built-in indexing for
large-scale graph queries, and does not scale to production graph sizes
the way a real graph database (e.g. Neo4j) would. Swapping to one later
would mean replacing this module's construction logic and
graph/retriever.py's traversal logic, while graph/models.py's typed
results should not need to change, since the rest of the application
never touches a NetworkX object directly.

Referential-integrity policy (why some missing references raise and
others don't): a Member's plan_id, a Claim's member_id, a Benefit's
plan_id, and a PriorAuthorization's member_id are treated as structurally
required -- every claim belongs to some member enrolled in some plan, by
definition of this domain, so a claim or authorization that references a
member_id with no Member record indicates a genuine data-integrity bug
and raises GraphBuildError. A Claim's provider_id and ordering_provider_id
are treated as optional and tolerated when unresolved: Case 5's claim
deliberately references a servicing provider_id ("PRV-9999") that does
not exist in the provider directory, to model incomplete real-world data.
When that happens, this builder skips the corresponding edge -- it never
invents a placeholder Provider node to paper over the gap. Service and
Network are not independently validated catalogs (there is no
services.json or networks.json); a Service node is created for whatever
service_code a Benefit, Claim, or PriorAuthorization references, and a
Network node is created for whatever network_name a Plan or Provider
carries, since those values are directly-supplied data, not foreign-key
references that could be "missing."
"""

from __future__ import annotations

import re

import networkx as nx

from tools.data_store import DataStore, get_data_store

# ---- node types --------------------------------------------------------------

MEMBER = "Member"
PLAN = "Plan"
CLAIM = "Claim"
BENEFIT = "Benefit"
SERVICE = "Service"
PROVIDER = "Provider"
NETWORK = "Network"
AUTHORIZATION = "PriorAuthorization"

# ---- relation types (structural facts only -- no business conclusions) ------

ENROLLED_IN = "ENROLLED_IN"
HAS_BENEFIT = "HAS_BENEFIT"
FOR_SERVICE = "FOR_SERVICE"
BELONGS_TO = "BELONGS_TO"
SERVICED_BY = "SERVICED_BY"
ORDERED_BY = "ORDERED_BY"
PARTICIPATES_IN = "PARTICIPATES_IN"
USES_NETWORK = "USES_NETWORK"
HAS_AUTHORIZATION = "HAS_AUTHORIZATION"


class GraphBuildError(RuntimeError):
    """Raised when the graph cannot be built because of a structurally
    impossible reference (e.g. a claim referencing a member_id that does
    not exist anywhere in the DataStore). Distinct from Case 5's
    deliberately incomplete provider references, which are tolerated by
    design -- see the module docstring."""


# ---- stable node id helpers ---------------------------------------------------


def member_node_id(member_id: str) -> str:
    return f"member:{member_id}"


def plan_node_id(plan_id: str) -> str:
    return f"plan:{plan_id}"


def claim_node_id(claim_id: str) -> str:
    return f"claim:{claim_id}"


def benefit_node_id(benefit_id: str) -> str:
    return f"benefit:{benefit_id}"


def service_node_id(service_code: str) -> str:
    return f"service:{service_code}"


def provider_node_id(provider_id: str) -> str:
    return f"provider:{provider_id}"


def authorization_node_id(authorization_id: str) -> str:
    return f"authorization:{authorization_id}"


def network_node_id(network_name: str) -> str:
    return f"network:{_slugify(network_name)}"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return slug.strip("-")


# ---- builder -------------------------------------------------------------------


def build_graph(store: DataStore | None = None) -> nx.MultiDiGraph:
    """Build the knowledge graph from a DataStore's validated records.

    A MultiDiGraph (not a plain DiGraph) because two distinct relations can
    exist between the same ordered pair of nodes in this schema in
    principle (e.g. a claim whose servicing and ordering provider happen to
    be the same provider would need both a SERVICED_BY and an ORDERED_BY
    edge to it); each edge's key is its relation name, so re-adding the
    same (source, relation, target) triple updates it in place rather than
    creating a duplicate, while two different relations between the same
    pair of nodes are kept as distinct parallel edges.
    """
    store = store or get_data_store()
    graph = nx.MultiDiGraph()

    _add_plans(graph, store)
    _add_providers(graph, store)
    _add_members(graph, store)
    _add_benefits(graph, store)
    _add_claims(graph, store)
    _add_authorizations(graph, store)

    return graph


def _add_plans(graph: nx.MultiDiGraph, store: DataStore) -> None:
    for plan in store.plans.values():
        graph.add_node(
            plan_node_id(plan.plan_id),
            node_type=PLAN,
            plan_id=plan.plan_id,
            plan_name=plan.plan_name,
            plan_type=plan.plan_type,
            network_name=plan.network_name,
        )
        # Plan.network_name is a required field -- always present, so this
        # edge always exists; there is no "missing network" case to tolerate.
        net_id = network_node_id(plan.network_name)
        if net_id not in graph:
            graph.add_node(net_id, node_type=NETWORK, name=plan.network_name)
        graph.add_edge(
            plan_node_id(plan.plan_id), net_id, key=USES_NETWORK, relation=USES_NETWORK
        )


def _add_providers(graph: nx.MultiDiGraph, store: DataStore) -> None:
    for provider in store.providers.values():
        graph.add_node(
            provider_node_id(provider.provider_id),
            node_type=PROVIDER,
            provider_id=provider.provider_id,
            name=provider.name,
            provider_type=provider.provider_type,
            specialty=provider.specialty,
            network_status=provider.network_status,
            network_name=provider.network_name,
        )
        if provider.network_name:  # e.g. Sunrise Physical Therapy Group has none
            net_id = network_node_id(provider.network_name)
            if net_id not in graph:
                graph.add_node(net_id, node_type=NETWORK, name=provider.network_name)
            graph.add_edge(
                provider_node_id(provider.provider_id),
                net_id,
                key=PARTICIPATES_IN,
                relation=PARTICIPATES_IN,
            )


def _add_members(graph: nx.MultiDiGraph, store: DataStore) -> None:
    for member in store.members.values():
        graph.add_node(
            member_node_id(member.member_id),
            node_type=MEMBER,
            member_id=member.member_id,
            first_name=member.first_name,
            last_name=member.last_name,
            date_of_birth=str(member.date_of_birth),
            plan_id=member.plan_id,
            status=member.status,
        )
        plan_id_node = plan_node_id(member.plan_id)
        if plan_id_node not in graph:
            raise GraphBuildError(
                f"Member {member.member_id} references unknown plan_id {member.plan_id!r}"
            )
        graph.add_edge(
            member_node_id(member.member_id), plan_id_node, key=ENROLLED_IN, relation=ENROLLED_IN
        )


def _add_benefits(graph: nx.MultiDiGraph, store: DataStore) -> None:
    for benefit in store.benefits.values():
        graph.add_node(
            benefit_node_id(benefit.benefit_id),
            node_type=BENEFIT,
            benefit_id=benefit.benefit_id,
            plan_id=benefit.plan_id,
            service_code=benefit.service_code,
            covered=benefit.covered,
            requires_prior_auth=benefit.requires_prior_auth,
            network_requirement=benefit.network_requirement,
            notes=benefit.notes,
        )

        plan_id_node = plan_node_id(benefit.plan_id)
        if plan_id_node not in graph:
            raise GraphBuildError(
                f"Benefit {benefit.benefit_id} references unknown plan_id {benefit.plan_id!r}"
            )
        graph.add_edge(
            plan_id_node,
            benefit_node_id(benefit.benefit_id),
            key=HAS_BENEFIT,
            relation=HAS_BENEFIT,
        )

        service_id = service_node_id(benefit.service_code)
        if service_id not in graph:
            graph.add_node(service_id, node_type=SERVICE, service_code=benefit.service_code)
        graph.add_edge(
            benefit_node_id(benefit.benefit_id), service_id, key=FOR_SERVICE, relation=FOR_SERVICE
        )


def _add_claims(graph: nx.MultiDiGraph, store: DataStore) -> None:
    for claim in store.claims.values():
        graph.add_node(
            claim_node_id(claim.claim_id),
            node_type=CLAIM,
            claim_id=claim.claim_id,
            member_id=claim.member_id,
            plan_id=claim.plan_id,
            service_code=claim.service_code,
            provider_id=claim.provider_id,
            ordering_provider_id=claim.ordering_provider_id,
            date_of_service=str(claim.date_of_service),
            status=claim.status,
            billed_amount=claim.billed_amount,
            allowed_amount=claim.allowed_amount,
            denial_reason_code=claim.denial_reason_code,
            denial_reason_description=claim.denial_reason_description,
        )

        member_id_node = member_node_id(claim.member_id)
        if member_id_node not in graph:
            raise GraphBuildError(
                f"Claim {claim.claim_id} references unknown member_id {claim.member_id!r}"
            )
        graph.add_edge(
            claim_node_id(claim.claim_id), member_id_node, key=BELONGS_TO, relation=BELONGS_TO
        )

        service_id = service_node_id(claim.service_code)
        if service_id not in graph:
            graph.add_node(service_id, node_type=SERVICE, service_code=claim.service_code)
        graph.add_edge(
            claim_node_id(claim.claim_id), service_id, key=FOR_SERVICE, relation=FOR_SERVICE
        )

        # Tolerated, not required: Case 5's claim references a servicing
        # provider_id ("PRV-9999") that does not exist. Skip the edge; never
        # invent a Provider node to fill the gap.
        servicing_provider_node = provider_node_id(claim.provider_id)
        if servicing_provider_node in graph:
            graph.add_edge(
                claim_node_id(claim.claim_id),
                servicing_provider_node,
                key=SERVICED_BY,
                relation=SERVICED_BY,
            )

        if claim.ordering_provider_id:
            ordering_provider_node = provider_node_id(claim.ordering_provider_id)
            if ordering_provider_node in graph:
                graph.add_edge(
                    claim_node_id(claim.claim_id),
                    ordering_provider_node,
                    key=ORDERED_BY,
                    relation=ORDERED_BY,
                )


def _add_authorizations(graph: nx.MultiDiGraph, store: DataStore) -> None:
    for auth in store.prior_authorizations.values():
        graph.add_node(
            authorization_node_id(auth.authorization_id),
            node_type=AUTHORIZATION,
            authorization_id=auth.authorization_id,
            member_id=auth.member_id,
            service_code=auth.service_code,
            plan_id=auth.plan_id,
            status=auth.status,
            effective_date=str(auth.effective_date),
            expiration_date=str(auth.expiration_date),
            notes=auth.notes,
        )

        member_id_node = member_node_id(auth.member_id)
        if member_id_node not in graph:
            raise GraphBuildError(
                f"PriorAuthorization {auth.authorization_id} references unknown "
                f"member_id {auth.member_id!r}"
            )
        graph.add_edge(
            member_id_node,
            authorization_node_id(auth.authorization_id),
            key=HAS_AUTHORIZATION,
            relation=HAS_AUTHORIZATION,
        )

        service_id = service_node_id(auth.service_code)
        if service_id not in graph:
            graph.add_node(service_id, node_type=SERVICE, service_code=auth.service_code)
        graph.add_edge(
            authorization_node_id(auth.authorization_id),
            service_id,
            key=FOR_SERVICE,
            relation=FOR_SERVICE,
        )
