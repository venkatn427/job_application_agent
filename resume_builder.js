const fs = require('fs');
const {
  Document, Packer, Paragraph, TextRun, AlignmentType, LevelFormat, BorderStyle,
  TabStopType, ExternalHyperlink,
} = require('docx');

const NAVY = '1F3864', BLUE = '2E5597', GRAY = '595959';
const FONT = 'Calibri';
const SZ = 19; // 10pt body
const RIGHT = 10800; // text width with 0.5" side margins on Letter

const r = (text, o = {}) => new TextRun({ text, font: FONT, size: o.size || SZ, ...o });
function rich(s, base = {}) {
  return s.split(/(\*\*[^*]+\*\*)/).filter(Boolean).map(p =>
    p.startsWith('**') ? r(p.slice(2, -2), { ...base, bold: true }) : r(p, base));
}

const section = t => new Paragraph({
  spacing: { before: 80, after: 40 },
  border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: NAVY, space: 1 } },
  keepNext: true,
  children: [r(t.toUpperCase(), { bold: true, color: NAVY, size: SZ + 3 })],
});
const bullet = s => new Paragraph({ numbering: { reference: 'b', level: 0 }, spacing: { after: 10 }, children: rich(s) });
const roleLine = (company, loc, dates) => new Paragraph({
  spacing: { before: 80, after: 0 }, keepNext: true,
  tabStops: [{ type: TabStopType.RIGHT, position: RIGHT }],
  children: [r(company, { bold: true }), r(loc ? '  |  ' + loc : '', { color: GRAY }), r('\t' + dates, { bold: true })],
});
const title = (t, sub) => new Paragraph({
  spacing: { after: 20 }, keepNext: true,
  children: [r(t, { italics: true, bold: true, color: BLUE }), ...(sub ? [r('  ' + sub, { italics: true, color: GRAY })] : [])],
});
const head = t => new Paragraph({
  spacing: { before: 30, after: 0 }, keepNext: true,
  children: [r(t, { bold: true, italics: true, color: GRAY })],
});



const D = {
  headline: ['Senior Data Engineer', 'Databricks, Spark & Lakehouse Platforms', 'Platform Reliability & Observability', 'AI-Ready Data'],
  summary: 'Senior data engineer with 9 years of hands-on experience building and running production data platforms on **Databricks, Spark, Delta Lake, Snowflake, and Kafka** across **Azure and AWS**. At Avaya, on a startup-style product team, I own the analytics data platform behind **Avaya Infinity**, a contact-center product for **40+ enterprise customers** across **100+ environments**, working directly with the VP of AI and Engineering. Before that, as a Deloitte consultant, I learned unfamiliar client systems quickly and owned workstreams from requirements to production for a state government client, Toyota, and Uber, after earlier work on Apple\'s ads platform, Citi, and Albertsons. I started my career in **HR / people analytics**, and I build with AI coding tools every day (**Claude Code, Codex, Cursor**).',
  highlights: [
    '**Lakehouse & pipelines:** Databricks, Delta Lake, Delta Live Tables, Spark, Trino, Kafka, Airflow, Azure Data Factory, Snowflake; batch, streaming, and CDC.',
    '**Platform & reliability:** Kubernetes, Helm, Argo CD, Terraform, CI/CD; Prometheus, Loki, Tempo, Grafana, OpenTelemetry; root-cause analysis and runbooks.',
    '**Security & governance:** Keycloak authentication and authorization, RBAC, three-layer row-level security, Unity Catalog, data quality (DQX, iCEDQ).',
    '**AI & automation:** AI Assistant on governed data, agent skills, debugging agents, and agentic workflows that fix and test production bugs in dev labs.',
  ],
  skills: [
    ['Languages', 'Python, SQL, PySpark, Scala, Java'],
    ['Databricks & Spark', 'Databricks, Delta Lake, Delta Live Tables, Unity Catalog, Spark SQL, Structured Streaming, Spark Connect, MLflow, DQX'],
    ['Data Platforms', 'Snowflake (Scripting, Snowpipe, Streams & Tasks), Trino, Kafka, Airflow, Azure Data Factory, Hive, DuckDB, Oracle, MySQL, MariaDB, MongoDB, SSIS'],
    ['Data Engineering', 'ETL/ELT, data modeling (dimensional, SCD Type 2), medallion architecture, incremental and idempotent loads, metadata-driven pipelines'],
    ['Kubernetes & IaC', 'Kubernetes (AKS, EKS), Helm, Argo CD, Docker, Terraform, Azure DevOps, GitHub Actions'],
    ['Observability', 'Prometheus, Grafana, Loki (Promtail), Tempo, OpenTelemetry Collector (Operator), Datadog, InfluxDB, Azure Monitor / Log Analytics'],
    ['Cloud', 'Azure (ADLS Gen2, ADF, AKS, Azure Databricks), AWS (S3, SNS, EMR, EKS, Glue, SageMaker)'],
    ['BI & Reporting', 'Apache Superset (as code), Power BI, Tableau, Looker, Grafana'],
    ['Security & Governance', 'Keycloak (OIDC, JWT), RBAC, row-level security, multi-tenant isolation, service principals, token rotation, Alation, iCEDQ'],
    ['AI & Tools', 'LLM applications, semantic layer for text-to-SQL, agentic workflows, agent skills, Playwright, Claude Code, Codex, Cursor (Bugbot)'],
  ],
  jobs: [
    { company: 'Avaya', loc: 'Dallas, TX', dates: 'Oct 2024 – Present', title: 'Senior Software Engineer, Analytics Data Platform', sub: '— Avaya Infinity (CCaaS)', groups: [
      { head: 'Lakehouse, pipelines, and data products', bullets: [
        'Built and own the analytics data platform (**Databricks, Spark, Delta Lake, Trino, Airflow, Superset**) on **Kubernetes** across **100+** dev, QA, and production environments for **40+ enterprise customers** (200 to 5,500 agents each).',
        'Moved the query layer from **Trino to Databricks** through a query-compatibility layer without breaking customer dashboards.',
        'Designed the gold-layer **agent, queue, and agent-status tables** that feed customer reporting on contact-center staff and operations.',
        'Modeled event-versioned and **SCD Type 2** history in Delta with MERGE, and wrote idempotent incremental loads (replaceWhere) that re-run safely without duplicates.',
        'Made the two core Spark jobs (every 15 minutes, all environments) **~55% faster** by fixing memory leaks and right-sizing executors, and moved them to **Spark Connect**.',
        'Cut Delta transaction-log size **~66%** and small files per batch by up to **200x** with write coalescing, compaction, and table-property tuning; moved select batch jobs from Spark to **DuckDB**.',
        'Fixed metric-correctness bugs (concurrency, join fan-out, duplicate events) and shipped each fix with an automated validation suite of **416 checks with zero mismatches** used for leadership sign-off.',
      ] },
      { head: 'Azure Data Factory, Databricks, and Delta Live Tables', bullets: [
        'Built metadata-driven **ADF pipelines** that copy source data into **ADLS Gen2**, then run Databricks notebooks and jobs to build bronze, silver, and gold tables.',
        'Set up ADF schedule and event triggers, retries, alerts, and run monitoring with service principals; added watermark-based incremental loads and tumbling window backfills.',
        'Validated incoming files (Get Metadata, If Condition, Switch), quarantined bad or late files, and built reusable parent and child pipelines with Git integration and ARM-template CI/CD.',
        'Built **Databricks Delta Live Tables** pipelines with Auto Loader, expectations for data-quality rules, and APPLY CHANGES for SCD Type 2 history in **Unity Catalog**, triggered from ADF.',
      ] },
      { head: 'Platform, infrastructure as code, and observability', bullets: [
        'Wrote **Terraform** to provision the full Databricks workspace, Unity Catalog catalogs, and access rules, and to deploy Airflow and Spark jobs, covering both infrastructure and services.',
        'Deployed platform services on Kubernetes with **Helm** and **Argo CD**, and automated Databricks object deployment and token rotation through **CI/CD**.',
        'Led the end-to-end **Airflow upgrade from 2.11 to 3.3**: migrated the metadata database on both **MySQL and MariaDB**, updated DAGs and custom plugins, and reworked the security and platform setup; built plugins that bring other services into one admin portal.',
        'Ran a **Datadog** proof of concept, then built the observability stack on **Prometheus, Loki, Tempo, and Grafana**, deployed with Terraform, with metrics, logs, and traces from Spark, Airflow, Trino, and Databricks and playbook-linked alerts.',
        'Collected traces with the **OpenTelemetry Collector Operator** into **Grafana Tempo** and shipped logs with **Promtail** into **Loki**, tying metrics, logs, and traces together in Grafana.',
        'Wrote a Python service that pulls Databricks warehouse state, billing, and job logs through the **REST API** (service principal, federated tokens) as a **Prometheus exporter** with Grafana dashboards.',
        'Added Spark event logging with Airflow links to Spark History Server, and caught a runaway History Server re-scan loop across 10+ environments before production, avoiding **$100K–150K/month** in cloud spend.',
        'Traced ADLS read traffic between Spark and Trino with **Azure Monitor** and **Log Analytics (KQL)**, and optimized Trino dashboard queries (removed full scans, cut authorization overhead **~40%**, resource groups by customer).',
        'Led **root-cause analysis** on streaming lag and stalled Kafka/CDC pipelines, turning incidents into permanent fixes and runbooks and cutting data incidents **~40%**.',
        'Built **CLI tooling** and automation scripts that connect to Kubernetes across multiple instances, services, and namespaces to debug support tickets and find the actual root cause.',
        'Built a daily **DQX** data-quality job (technical and business rules) on the gold tables with a daily dashboard; set platform benchmark criteria and cache warm-up for the main dashboards.',
      ] },
      { head: 'Security, reporting, and AI', bullets: [
        'Owned **authentication and authorization**: Keycloak sign-in, **RBAC** managed from the platform portal, JWT-secured embedded pages, and **row-level security** at three layers, plus per-customer **Unity Catalog** hardening for day-0/1/2 operations.',
        'Built Superset **dashboards as code** with tenant-separated metadata, a semantic metric layer published as a data dictionary, a JWT-based embedding gateway for new portal pages, and a file-upload enrichment feature.',
        'Built the backend for an **AI Assistant** on an OpenAI model self-hosted on Databricks (moved from an external endpoint), with a semantic layer so it writes correct SQL on each customer\'s own data.',
        'Built **agentic workflows** that take bugs reported in production and fix them in dev labs: agent skills generate simulated scenarios based on the bug, then verify fixes with **Playwright**, pipeline execution tests, test cases, and unit tests.',
        'Built debugging agents that trace issues to root cause, agent skills and a local agentic framework that runs the full pipeline with one command, and rolled out **Cursor Bugbot** for code reviews.',
        '**Mentored 4 engineers** through design reviews while staying hands-on in core code.',
      ] },
    ] },
    { company: 'Deloitte', loc: 'Remote', dates: 'Sep 2022 – Oct 2024', title: 'Senior Data Engineer (Consultant)', groups: [
      { head: 'Client: CDLA (state government) — Oracle to Snowflake migration', bullets: [
        'Migrated a state government Oracle warehouse to **Snowflake**: **300+ tables** and **100+ stored procedures** rewritten in Snowflake Scripting, with staging, bronze, silver, and gold layers.',
        'Wrote a **config-driven Python framework** that generated procedures, tables, and load jobs from templates, cutting manual work **~40%**.',
        'Automated deployment of version-controlled Snowflake scripts and AWS objects across environments with **Azure DevOps CI/CD**.',
        'Built event-driven ingestion with **Snowpipe, S3, and SNS** (data **~30% fresher**) and incremental processing with **Streams & Tasks** (latency **~20%** lower), plus monitoring and alerting with Airflow and Snowflake metadata.',
        'Built ingestion and transformation pipelines for CSV, JSON, Kafka, and Salesforce sources, producing canonical datasets for analytics, reporting, and ML.',
      ] },
      { head: 'Client: Toyota — supply chain analytics', bullets: [
        'Designed a **Databricks / PySpark warehouse** that unified multi-source supply chain data into governed, reusable models, and tuned Spark ETL to cut load times **~40%**.',
        'Built data-quality and audit frameworks that track job metrics, improving pipeline efficiency **~20%** and cutting processing time **~15%**.',
        'Consolidated similar pipelines into shared code, finishing runs **~40%** faster and using **~25%** less storage.',
        'Built event-driven Order Management System loads with **Airflow, S3, and SageMaker** for predictive analytics, and routed logs to **Datadog**.',
      ] },
      { head: 'Client: Uber — infrastructure insights', bullets: [
        'Built **Airflow, Spark, and Hive** pipelines that processed **millions of Kafka events an hour** across data centers, plus ETL for **100K+ JSON files/day** (900+ columns).',
        'Wrote an API client that turned infrastructure logs into insights for Uber\'s resource-allocation platform, cutting reporting time **~25%**, and modeled usage and budget data for forecasting.',
        'Moved **50+ dashboards** from Looker to Tableau, cutting navigation time **~30%**.',
      ] },
    ] },
    { company: 'Wipro', loc: 'Austin, TX', dates: 'Sep 2021 – Sep 2022', title: 'Project Lead, Big Data (Client: Apple)', groups: [
      { bullets: [
        'Led production support for **Spark Structured Streaming** apps on Apple\'s ads platform (**600M+ events/day**), raising efficiency from **30% to 75%** and SLA adherence by **50%**, protecting **$100K+/month** in ad revenue.',
        'Stabilized streaming and batch workloads on **AWS EKS, EMR, and S3** with Airflow, across cloud and on-prem deployments.',
        'Built **Grafana and InfluxDB** dashboards that flagged processing delays and latency spikes before they hit revenue.',
        'Wrote scheduled **Python** checks that compared data across databases and sent alerts on critical inconsistencies.',
        'Worked with business and engineering teams to turn requirements into pipelines and keep metrics consistent across the ads platform.',
      ] },
    ] },
    { company: 'TCS America', loc: 'Phoenix, AZ / Tampa, FL', dates: 'Sep 2018 – Sep 2021', title: 'Data Engineer (Clients: Albertsons, Citi)', groups: [
      { head: 'Client: Albertsons — retail analytics on Azure', bullets: [
        'Streamed structured and semi-structured (XML/JSON) data from **Kafka into Snowflake** with **Azure Data Factory**, ADLS, and **Snowpipe**.',
        'Built Scala and **PySpark** pipelines on **Azure Databricks**, orchestrated with ADF, that load curated datasets into Snowflake for retail analytics.',
        'Automated **Alation** catalog and lineage curation in Python with business stakeholders, and enforced data-quality rules in **iCEDQ**.',
        'Worked with cross-functional teams to troubleshoot and optimize pipeline performance, and wrote onboarding documentation.',
      ] },
      { head: 'Client: Citi (financial services) — anti-money-laundering and marketing data', bullets: [
        'Managed ETL for **8K–10K batch jobs/day** and **200K+ executions/month**, moving millions of records daily into Citi\'s AML systems, and moved key flows to **Spark Streaming**.',
        'Designed distributed **Spark** pipelines from **Hive to MongoDB** and built canonical datasets for ML models and targeted marketing.',
        'Automated manual data workflows in **Python**, cutting manual effort **~90%** while improving consistency and compliance.',
      ] },
    ] },
    { company: 'Epathusa', loc: 'Des Moines, IA', dates: 'Jan 2018 – Aug 2018', title: 'Data Engineer (HR / People Analytics)', groups: [
      { bullets: [
        'Built **SSIS** ETL packages that pulled employee, job, and organization data from HR source systems into a central **SQL Server HR data repository**.',
        'Modeled HR data into fact and dimension tables and kept employee history with slowly changing dimensions, so headcount could be reported as of any date.',
        'Wrote **SQL** for headcount, new hires, terminations, attrition and turnover, and tenure by department, location, and job level.',
        'Built **Power BI** and Tableau dashboards for HR with row-level security by manager, and reconciled loads against HR source reports.',
      ] },
    ] },
    { company: 'Southern Arkansas University', loc: 'Magnolia, AR', dates: 'Jan 2017 – May 2018', title: 'Graduate Research Assistant', groups: [] },
  ],
};

function build(cfg, out) {
  const kids = [];
  kids.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 0 },
    children: [r('Venkat Nelluri', { bold: true, color: NAVY, size: 36 })] }));
  const link = (url, text) => new ExternalHyperlink({ link: url, children: [r(text, { size: 18 })] });
  kids.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 10 },
    children: [
      r('Dallas, TX  |  +1 510-953-2111  |  ', { size: 18 }),
      link('mailto:vnelluri2430@gmail.com', 'vnelluri2430@gmail.com'), r('  |  ', { size: 18 }),
      link('https://www.linkedin.com/in/vnelluri/', 'linkedin.com/in/vnelluri'), r('  |  ', { size: 18 }),
      link('https://github.com/venkatn427', 'github.com/venkatn427'),
    ] }));
  kids.push(new Paragraph({ alignment: AlignmentType.CENTER, spacing: { after: 30 },
    children: [r(cfg.headline.join('  |  '), { bold: true, color: BLUE, size: 19 })] }));

  kids.push(section('Professional Summary'));
  kids.push(new Paragraph({ spacing: { after: 20 }, alignment: AlignmentType.JUSTIFIED, children: rich(cfg.summary) }));
  (cfg.highlights || []).forEach(h => kids.push(bullet(h)));

  kids.push(section('Technical Skills'));
  cfg.skills.forEach(([k, v]) => kids.push(new Paragraph({ spacing: { after: 0 },
    children: [r(k + ': ', { bold: true }), r(v)] })));

  kids.push(section('Professional Experience'));
  cfg.jobs.forEach(j => {
    kids.push(roleLine(j.company, j.loc, j.dates));
    kids.push(title(j.title, j.sub));
    j.groups.forEach(g => { if (g.head) kids.push(head(g.head)); g.bullets.forEach(b => kids.push(bullet(b))); });
  });

  kids.push(section('Education'));
  kids.push(new Paragraph({ spacing: { after: 0 }, children: rich(
    '**M.S., Computer & Information Science**, Southern Arkansas University (2018)   |   **B.Tech, Electronics & Communication Engineering**, JNTU Kakinada, India (2015)') }));

  const doc = new Document({
    creator: 'Venkat Nelluri', title: 'Venkat Nelluri – Resume',
    styles: { default: { document: { run: { font: FONT, size: SZ } } } },
    numbering: { config: [{ reference: 'b', levels: [{ level: 0, format: LevelFormat.BULLET, text: '•',
      alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 270, hanging: 180 } } } }] }] },
    sections: [{ properties: { page: { size: { width: 12240, height: 15840 },
      margin: { top: 480, bottom: 480, left: 660, right: 660 } } }, children: kids }],
  });
  return Packer.toBuffer(doc).then(b => fs.writeFileSync(out, b));
}

build(D, 'Venkat_Nelluri_Master_Resume.docx').then(() => console.log('ok'));
