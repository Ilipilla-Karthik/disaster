# Architecture Diagram
`mermaid
graph TD
    A[Emergency Sources] --> B[Incident Intake Agent]
    B --> C[Duplicate Detection]
    C --> D[Situation Assessment Agent]
    D --> E[Weather Agent]
    D --> F[Geospatial & Accessibility Agent]
    G[Resource Management Agent] --> H[Allocation Agent]
    E --> H
    F --> H
    D --> H
    H --> I[Coordination Agent]
    I --> J[Human Approval]
    J --> K[Action Tracking]
    I --> L[Reviewer Agent]
    L --> D
    K --> M[Dynamic Replanning]
`
