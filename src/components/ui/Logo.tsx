import Image from "next/image";

type LogoProps = {
  variant?: "color" | "white";
  className?: string;
  priority?: boolean;
};

export default function Logo({ variant = "color", className = "h-12 w-auto", priority }: LogoProps) {
  const src = variant === "white" ? "/brand/zoiko-logo-white.png" : "/brand/zoiko-logo.png";
  return (
    <Image
      src={src}
      alt="Zoiko Access"
      width={variant === "white" ? 1213 : 964}
      height={variant === "white" ? 432 : 343}
      className={className}
      priority={priority}
    />
  );
}
