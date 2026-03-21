{{/*
Expand the name of the chart.
*/}}
{{- define "auto-sre-agent.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "auto-sre-agent.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "auto-sre-agent.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "auto-sre-agent.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{ include "auto-sre-agent.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "auto-sre-agent.selectorLabels" -}}
app.kubernetes.io/name: {{ include "auto-sre-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
