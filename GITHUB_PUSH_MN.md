# GitHub руу оруулах заавар (LabSmart)

## Шинэ GitHub repository-д анх удаа push хийх

1. Энэ хавтсыг ZIP-ээс задална.
2. GitHub дээр **New repository** үүсгэнэ. Repository хоосон байвал хамгийн амар.
3. PowerShell-ийг энэ хавтас дотор нээгээд дараах командыг ажиллуулна:

```powershell
git init
git add .
git status
git commit -m "Initial commit: LabSmart project and report"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
git push -u origin main
```

`YOUR-USERNAME` болон `YOUR-REPOSITORY` хэсгийг өөрийн GitHub хэрэглэгчийн нэр, repository нэрээр солино.

## Одоо байгаа Git repository-д нэмэх

Хэрэв энэ хавтас чинь аль хэдийн Git repository бол `git init` болон `git remote add origin` командыг дахин бүү ажиллуул. Дараахыг ашиглана:

```powershell
git status
git add .
git status
git commit -m "docs: add LabSmart project report"
git push
```

## Анхаарах зүйл

- `.env`, `.venv`, `node_modules`, Python cache болон локал тохиргоонууд Git-д орохгүй.
- `app/data/lab.db` нь ажиллуулахад ашиглагддаг одоогийн demo өгөгдөл тул төслийн хувилбарт хадгалсан.
- README дахь `admin` болон `viewer` нэвтрэх мэдээлэл нь demo орчинд зориулагдсан. Production серверт шууд бүү ашигла; нууц үг, өгөгдөл, тохиргоог бодит хэрэглээнд зориулж өөрчил.
- GitHub нэвтрэлт асуувал Git Credential Manager эсвэл GitHub-ийн дэмждэг authentication аргыг ашигла. Нууц үг, token-оо чатад бүү явуул.
