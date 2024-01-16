## Prereq
```bash
cd /tnwks-ops/infrastructure
aws sso login --profile default
#run decrypt / encrypt here

cd /tnwks-ops/kubernetes
#run decrypt / encrypt here
```
### sops decrypt all files in working dir (recursive)
```bash
for file in $(find . -name "*.sops.yaml" ! -path "./tmpl/*");
do
  sops -d $file > $file.dec;
  mv $file.dec $file
done

```

## sops encrypt all files in working dir (recursive)
```bash
for file in $(find . -name "*.sops.yaml" ! -path "./tmpl/*");
do
  sops -e -i $file;
done

```
