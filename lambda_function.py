import json
import boto3
import csv
import io
import time
from datetime import datetime

s3 = boto3.client('s3')
bedrock = boto3.client('bedrock-runtime', region_name='ap-south-1')

MODEL_ID = 'anthropic.claude-3-haiku-20240307-v1:0'

def classify_log_bedrock(log_message):
    """Classify a log message using Claude 3 Haiku via Amazon Bedrock."""
    prompt = f"""You are a log classification system. Classify the following log message into exactly one of these categories: critical, warning, or info.

Log message: {log_message}

Reply with only one word — either: critical, warning, or info. No explanation."""

    response = bedrock.invoke_model(
        modelId=MODEL_ID,
        body=json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": prompt}]
        }),
        contentType='application/json',
        accept='application/json'
    )
    result = json.loads(response['body'].read())
    label = result['content'][0]['text'].strip().lower()
    if label not in ['critical', 'warning', 'info']:
        label = 'info'
    return label


def classify_log_rules(log_message):
    """Fallback rule-based classifier (used when Bedrock is unavailable)."""
    msg = log_message.lower()

    critical_keywords = [
        'error', 'exception', 'failed', 'failure', 'critical',
        'crash', 'fatal', 'timeout', 'rollback', 'nullpointer',
        'connection refused', 'out of memory', 'stack overflow'
    ]
    warning_keywords = [
        'warn', 'warning', 'latency', 'slow', 'retry',
        'threshold', 'high', 'deprecated', 'reorder', 'below'
    ]

    for kw in critical_keywords:
        if kw in msg:
            return 'critical'
    for kw in warning_keywords:
        if kw in msg:
            return 'warning'
    return 'info'


def classify_log(log_message, use_bedrock=True):
    """Classify a log message, with optional Bedrock or rule-based fallback."""
    if use_bedrock:
        try:
            return classify_log_bedrock(log_message)
        except Exception as e:
            print(f"Bedrock error, falling back to rules: {e}")
            return classify_log_rules(log_message)
    return classify_log_rules(log_message)


def lambda_handler(event, context):
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = event['Records'][0]['s3']['object']['key']

    print(f"Processing file: s3://{bucket}/{key}")

    # Read the uploaded CSV from raw/
    response = s3.get_object(Bucket=bucket, Key=key)
    content = response['Body'].read().decode('utf-8')

    # Strip BOM and normalize line endings
    content = content.lstrip('\ufeff').replace('\r\n', '\n').replace('\r', '\n')

    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)

    if not rows:
        print("ERROR: CSV parsed 0 rows. Check file encoding.")
        return {'statusCode': 400, 'body': 'Empty CSV or encoding issue.'}

    results = []
    for row in rows:
        log_msg = row.get('message', row.get('log', str(row)))
        label = classify_log(log_msg, use_bedrock=True)
        time.sleep(0.5)  # avoid Bedrock throttling
        results.append({**row, 'classification': label})
        print(f"Classified: {log_msg[:60]} => {label}")

    # Write classified results to processed/
    output_key = (
        key.replace('raw/', 'processed/')
           .replace('.csv', f'_classified_{datetime.now().strftime("%Y%m%d%H%M%S")}.csv')
    )
    fieldnames = list(results[0].keys())
    out_buf = io.StringIO()
    writer = csv.DictWriter(out_buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(results)

    s3.put_object(
        Bucket=bucket,
        Key=output_key,
        Body=out_buf.getvalue(),
        ContentType='text/csv'
    )
    print(f"Saved classified output to: s3://{bucket}/{output_key}")

    return {'statusCode': 200, 'body': f'Classified {len(results)} log entries.'}
