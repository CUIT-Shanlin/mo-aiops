{{- define "mo-chat-aiops.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "mo-chat-aiops.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "mo-chat-aiops.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "mo-chat-aiops.labels" -}}
helm.sh/chart: {{ include "mo-chat-aiops.chart" . }}
app.kubernetes.io/name: {{ include "mo-chat-aiops.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "mo-chat-aiops.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mo-chat-aiops.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "mo-chat-aiops.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "mo-chat-aiops.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "mo-chat-aiops.secretName" -}}
{{- default (printf "%s-secret" (include "mo-chat-aiops.fullname" .)) .Values.secret.name -}}
{{- end -}}
