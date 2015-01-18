To upload blobs
---------------
cd /mnt/gcs-bucket
find . -type f > /tmp/blobs
gsutil -m  -h "Cache-Control:public, max-age=31536000" \
  cp -a public-read -z blob -I gs://blink-gitcs/ < /tmp/blobs

Amazon S3:
Get https://github.com/twpayne/s3-parallel-put
~/s3p --bucket=blink-gitcs --put=stupid --content-type=octect/stream --gzip --secure --grant public-read --header="Cache-Control:public, max-age=31536000" .


GCE VM
------
gcloud compute instances create "blink-git" \
  --zone "europe-west1-c" --machine-type "n1-standard-8" \
  --network "default" --no-restart-on-failure --maintenance-policy "TERMINATE" \
  --scopes "https://www.googleapis.com/auth/devstorage.read_write" \
  --disk "name=blink-git-micro" "device-name=blink-git-micro" "mode=rw" "boot=yes" \
  --disk "name=blink-git-loose" "device-name=blink-git-loose" "mode=rw" "boot=no"
